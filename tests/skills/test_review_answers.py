import copy
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "docs" / "usecases" / "skills" / "og-answer-review"
SCRIPT = SKILL / "scripts" / "review_answers.py"
STANDARD = json.loads((SKILL / "standard" / "standard.json").read_text(encoding="utf-8"))
EXAMPLE = json.loads((SKILL / "examples" / "answers.json").read_text(encoding="utf-8"))

_spec = importlib.util.spec_from_file_location("review_answers", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["review_answers"] = mod
_spec.loader.exec_module(mod)


def run_cli(*args, stdin=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def answer(body, result="ActionsTaken", questions=(("Уличное освещение", "0005.0005"),), days_late=0,
           greeting="Уважаемая Анна Сергеевна!", sign="С уважением,\nИ. И. Иванов", **kw):
    a = {"id": kw.pop("id", 1), "registration_number": "1-ИСХ", "registration_date": "2026-09-20", "result": result,
         "prepared_by": kw.pop("prepared_by", "Смирнов А. В."),
         "requests": [{"registration_number": "1-ОГ", "days_late": days_late,
                       "questions": [{"question": q, "code": c, "review_result": result} for q, c in questions]}],
         "text": f"Бланк министерства\nНа № ___ от ___\n{greeting}\n{body}\n{sign}"}
    a.update(kw)
    return a


def one(a, standard=STANDARD):
    return mod.review([a], standard)["answers"][0]


GOOD = ("По Вашему обращению об уличном освещении во дворе дома № 7 по ул. Садовой сообщаем: 18.09.2026 подрядчик "
        "установил четыре новых светильника и восстановил освещение во дворе, все они работают.")


def test_example_matches_expected():
    expected = json.loads((SKILL / "examples" / "answers.expected.json").read_text(encoding="utf-8"))
    assert mod.review(EXAMPLE, STANDARD, str(mod.DEFAULT_STANDARD)) == expected


def test_example_verdicts():
    res = mod.review(EXAMPLE, STANDARD)
    verdicts = {a["registration_number"]: a["verdict"] for a in res["answers"]}
    assert verdicts == {"101-ИСХ": "По существу", "102-ИСХ": "Отписка", "103-ИСХ": "Отписка",
                        "104-ИСХ": "Ответ с нарушениями", "105-ИСХ": "Нет текста"}
    assert res["summary"]["bad"] == 3 and res["summary"]["without_text"] == 1 and res["summary"]["bad_share"] == 0.75
    assert res["standard"]["name"].startswith("Стандарт качества") and res["standard"]["version"] == "1.0"


def test_good_answer():
    r = one(answer(GOOD))
    assert r["verdict"] == "По существу" and r["reasons"] == []
    assert set(r["criteria"]["specificity"]["metrics"]["kinds"]) >= {"дата", "адрес", "действие"}
    assert r["criteria"]["coverage"]["status"] == "ok"


def test_otpiska_and_short():
    r = one(answer("Ваше обращение рассмотрено в установленном порядке. Вопрос находится на контроле."))
    assert r["verdict"] == "Отписка" and r["criteria"]["otpiska"]["status"] == "violation"
    assert any("установленном порядке" in e for e in r["criteria"]["otpiska"]["evidence"])
    assert one(answer("Текст письма"))["verdict"] == "Отписка"


def test_body_extraction_takes_real_greeting_and_stops_at_signature():
    text = ("Шапка\nУважаемая (ый) ______!\nУважаемая Анна Сергеевна!\n" + GOOD + "\nС уважением,\nподпись\nПриложение: схема")
    body, greeting, tail, notes = mod.split_body(text, mod.load_standard(STANDARD))
    assert greeting == "Уважаемая Анна Сергеевна!" and body == GOOD and notes == []
    body, greeting, _, notes = mod.split_body("Без обращения\n" + GOOD, mod.load_standard(STANDARD))
    assert greeting == "" and "Не найдено обращение" in notes[0] and GOOD in body


def test_form_placeholders_informal_greeting():
    r = one(answer(GOOD + " Всё ок, шеф.", greeting="Уважаемая (ый) ________!", sign="<Должность подписанта>\n<Подписант>"))
    form = r["criteria"]["form"]
    assert form["status"] == "violation" and form["metrics"]["placeholders"] >= 3
    assert any("шеф" in f for f in form["findings"]) and any("по имени" in f for f in form["findings"])
    assert r["verdict"] == "Ответ с нарушениями"


def test_coverage_none_partial():
    q = (("Уличное освещение", "0005.0005"), ("Вывоз мусора с контейнерных площадок", "0005.0006"))
    r = one(answer(GOOD, questions=q))
    assert r["criteria"]["coverage"]["status"] == "remark" and "мусора" in " ".join(r["criteria"]["coverage"]["findings"])
    r = one(answer(GOOD, questions=(("Выборы в органы местного самоуправления", "0001.0001"),)))
    assert r["criteria"]["coverage"]["status"] == "violation"
    assert one(answer(GOOD, questions=()))["criteria"]["coverage"]["status"] == "n/a"


def test_result_consistency():
    vague = "Сообщаем, что по Вашему вопросу об уличном освещении проведена проверка 12.09.2026 по ул. Садовой."
    r = one(answer(vague + " Освещение во дворе работает.", result="Denied"))
    rc = r["criteria"]["result_consistency"]
    assert rc["status"] == "violation" and "отказ без основания" in rc["findings"]
    r = one(answer("В связи с отсутствием оснований в установке освещения отказано. Решение можно обжаловать в суде, "
                   "уличное освещение по ул. Садовой входит в план на 2027 год.", result="Denied"))
    assert r["criteria"]["result_consistency"]["status"] == "ok"
    r = one(answer("По вопросу уличного освещения во дворе по ул. Садовой информация учтена, письмо от 12.09.2026.",
                   result="ActionsTaken"))
    assert "нет конкретных мер" in " ".join(r["criteria"]["result_consistency"]["findings"])


def test_specificity_soft_for_explained():
    text = "Разъясняем, что порядок подключения уличного освещения определяют правила благоустройства; вы вправе обратиться в администрацию."
    assert one(answer(text, result="Explained"))["criteria"]["specificity"]["status"] == "remark"
    assert one(answer(text, result="ActionsTaken"))["criteria"]["specificity"]["status"] == "violation"


def test_formalism_metrics():
    text = ("В целях обеспечения надлежащего осуществления полномочий в рамках действующего порядка рассмотрение данного "
            "вопроса уличного освещения производится в соответствии с установленными требованиями к осуществлению проведения работ.")
    f = one(answer(text))["criteria"]["formalism"]
    assert f["status"] == "violation" and f["metrics"]["kanceliarisms_per_1000"] > 45
    assert f["metrics"]["sentences"] == 1


def test_timeliness_and_pd():
    r = one(answer(GOOD, days_late=3))
    assert r["criteria"]["timeliness"]["status"] == "violation" and r["verdict"] == "Ответ с нарушениями"
    r = one(answer(GOOD + " Звоните заявителю по номеру +7 912 345-67-89."))
    assert r["criteria"]["personal_data"]["status"] == "remark"


def test_standard_controls_rules():
    std = copy.deepcopy(STANDARD)
    std["criteria"]["otpiska"]["patterns"].append({"re": "светильник", "weight": 5, "hint": "тестовая фраза"})
    assert one(answer(GOOD), std)["verdict"] == "Отписка"
    std = copy.deepcopy(STANDARD)
    std["criteria"]["timeliness"]["enabled"] = False
    r = one(answer(GOOD, days_late=10), std)
    assert "timeliness" not in r["criteria"] and r["verdict"] == "По существу"
    std = copy.deepcopy(STANDARD)
    std["default_verdict"] = "Хорошо"
    assert one(answer(GOOD), std)["verdict"] == "Хорошо"


def test_bad_standard():
    for broken in ({}, {"criteria": {}}, {**STANDARD, "verdicts": [{"label": "X", "any_violation": ["nope"]}]}):
        with pytest.raises(mod.InputError):
            mod.review([answer(GOOD)], broken)
    std = copy.deepcopy(STANDARD)
    std["criteria"]["otpiska"]["patterns"] = [{"re": "(", "weight": 1}]
    with pytest.raises(mod.InputError, match="регулярное выражение"):
        mod.review([answer(GOOD)], std)


def test_input_shapes_and_pages():
    a, b = answer(GOOD, id=1), answer(GOOD, id=2, prepared_by="Кузнецова Е. С.")
    assert mod.review({"answers": [a, b]}, STANDARD)["summary"]["answers"] == 2
    assert mod.review([{"answers": [a]}, {"answers": [b, a]}], STANDARD)["summary"]["answers"] == 2  # страницы, дубль
    assert mod.review(a, STANDARD)["summary"]["answers"] == 1
    for bad in ({"x": 1}, "s", [], [1]):
        with pytest.raises(mod.InputError):
            mod.review(bad, STANDARD)


def test_grouping():
    res = mod.review([answer(GOOD, id=1), answer("Текст письма", id=2), answer("Текст письма", id=3, prepared_by="Б")], STANDARD)
    by = {g["key"]: g for g in res["by_prepared_by"]}
    assert by["Смирнов А. В."]["answers"] == 2 and by["Смирнов А. В."]["bad"] == 1 and by["Б"]["bad_share"] == 1.0
    assert res["by_topic"][0]["key"] == "0005.0005"


def test_large_is_fast():
    data = [answer(GOOD * 3, id=i) for i in range(600)]
    t0 = time.perf_counter()
    mod.review(data, STANDARD)
    assert time.perf_counter() - t0 < 20


def test_markdown_and_xlsx_spec():
    res = mod.review(EXAMPLE, STANDARD)
    md = mod.to_markdown(res, 2)
    assert "# Качество ответов" in md and "## Ответы с проблемами" in md and "Начало ответа" in md
    spec = mod.to_xlsx_spec(res)
    assert [s["name"] for s in spec["sheets"]] == ["Ответы", "По исполнителям"] and len(spec["sheets"][0]["rows"]) == 5


def test_xlsx_spec_builds_with_excel_skill(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    s = importlib.util.spec_from_file_location("bx_review", ROOT / "docs" / "usecases" / "skills" / "excel-report" / "scripts" / "build_xlsx.py")
    bx = importlib.util.module_from_spec(s)
    s.loader.exec_module(bx)
    out = bx.build(mod.to_xlsx_spec(mod.review(EXAMPLE, STANDARD)), str(tmp_path / "r.xlsx"))
    assert openpyxl.load_workbook(out).sheetnames == ["Ответы", "По исполнителям"]


def test_cli(tmp_path):
    ex = SKILL / "examples" / "answers.json"
    code, out, _ = run_cli(str(ex))
    assert code == 0 and json.loads(out)["summary"]["answers"] == 5
    code, out, _ = run_cli(str(ex), "--format", "md", "--examples", "1")
    assert code == 0 and "Стандарт:" in out
    code, out, _ = run_cli(str(ex), "--format", "xlsx-spec")
    assert code == 0 and json.loads(out)["sheets"][0]["name"] == "Ответы"
    custom = tmp_path / "std.json"
    std = copy.deepcopy(STANDARD)
    std["name"], std["version"] = "Стандарт органа", "2.3"
    custom.write_text(json.dumps(std, ensure_ascii=False), encoding="utf-8")
    code, out, _ = run_cli(str(ex), "--standard", str(custom))
    assert code == 0 and json.loads(out)["standard"]["version"] == "2.3"
    code, _, err = run_cli(str(ex), "--standard", str(tmp_path / "нет.json"))
    assert code == 2 and "не найден" in err
    code, _, err = run_cli("-", "--standard", "-")
    assert code == 2 and "stdin" in err
    code, _, err = run_cli("-", stdin=b"{")
    assert code == 2 and "не JSON" in err
    code, _, err = run_cli(str(ex), "--examples", "99")
    assert code == 2 and "--examples" in err
