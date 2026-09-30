import importlib.util
import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "docs" / "usecases" / "skills" / "kd-resolution"
SCRIPT = SKILL / "scripts" / "suggest_resolution.py"
HISTORY = json.loads((SKILL / "examples" / "history.json").read_text(encoding="utf-8"))
TITLE = "Вх. письмо Аппарата Правительства РФ «О мониторинге цифровых сервисов»"

_spec = importlib.util.spec_from_file_location("suggest_resolution", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["suggest_resolution"] = mod
_spec.loader.exec_module(mod)

TODAY = date(2026, 9, 30)


def run_cli(*args, stdin=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def item(id_, source, assignee, created="2026-09-01", ctl=False, co=(), parts=(), term=10, text="Прошу подготовить ответ.", sup=None):
    return {"id": id_, "created": created, "subject": source, "text": text, "source_document": {"name": source},
            "assignee": assignee, "co_assignees": list(co), "parts_assignees": list(parts), "under_control": ctl,
            "supervisor": sup, "term_days": term}


def test_example_matches_expected():
    text = (SKILL / "examples" / "incoming.txt").read_text(encoding="utf-8")
    expected = json.loads((SKILL / "examples" / "incoming.expected.json").read_text(encoding="utf-8"))
    assert mod.suggest(text, HISTORY, TITLE, TODAY) == expected


def test_example_picks_government_executor_with_control():
    text = (SKILL / "examples" / "incoming.txt").read_text(encoding="utf-8")
    r = mod.suggest(text, HISTORY, TITLE, TODAY)
    assert r["assignee"]["name"] == "Иванов И. И." and r["confidence"] == "высокая"
    assert [c["name"] for c in r["co_assignees"]] == ["Смирнов А. В."]
    assert r["control"]["recommend"] and r["control"]["supervisor"] == "Орлова Е. В."
    assert r["deadline"]["proposed"] == "2026-10-15" and r["deadline"]["document_deadlines"] == ["2026-10-20"]
    assert r["resolution_draft"].splitlines()[0] == "Иванов И. И. (отв.), Смирнов А. В."


@pytest.mark.parametrize("text,who", [
    ("Прошу проверить работу прививочных кабинетов в поликлиниках.", "Петрова В. А."),
    ("О проведении олимпиады по математике среди школьников.", "Мокрушин И. С."),
    ("Жалоба на ямочный ремонт дороги, прошу принять меры по ремонту дорог.", "Смирнов А. В."),
])
def test_topic_routing(text, who):
    r = mod.suggest(text, HISTORY, "", TODAY)
    assert r["assignee"]["name"] == who
    assert not r["control"]["recommend"]


def test_roads_bring_regular_co_assignee():
    r = mod.suggest("Прошу рассмотреть ремонт автомобильных дорог и ямочный ремонт.", HISTORY, "", TODAY)
    assert [c["name"] for c in r["co_assignees"]] == ["Кузнецова Е. С."]


def test_unknown_topic_low_confidence():
    r = mod.suggest("Приглашение на выставку народных промыслов и ремёсел.", HISTORY, "", TODAY)
    assert r["confidence"] == "низкая" and r["assignee"] is None
    assert r["author_frequent_assignees"][0]["name"] == "Петрова В. А."
    assert any("Похожих поручений мало" in n for n in r["notes"])
    assert "Прошу рассмотреть и подготовить предложения." in r["resolution_draft"]


def test_control_rules():
    hist = [item(i, "Письмо о ремонте дорог", "Смирнов", ctl=False) for i in range(6)]
    # Привычка автора перевешивает косвенные признаки, когда похожих достаточно.
    r = mod.suggest("О ремонте дорог. Прошу представить информацию в срок до 20.10.2026.", hist, "", TODAY)
    assert not r["control"]["recommend"] and any("обычно не ставит" in x for x in r["control"]["reasons"])
    # Прямое указание — всегда на контроль.
    assert mod.suggest("О ремонте дорог. Взять на контроль.", hist, "", TODAY)["control"]["recommend"]
    # Вышестоящий орган со сроком — на контроль независимо от привычки.
    assert mod.suggest("Правительство: о ремонте дорог, доложить до 20.10.2026.", hist, "", TODAY)["control"]["recommend"]
    # Мало похожих: признаки текста решают.
    few = [item(1, "Письмо о ремонте дорог", "Смирнов")]
    assert mod.suggest("О ремонте дорог. Прошу представить информацию в срок до 20.10.2026.", few, "", TODAY)["control"]["recommend"]
    assert not mod.suggest("О ремонте дорог.", few, "", TODAY)["control"]["recommend"]
    # Автор ставит похожее на контроль.
    ctl = [item(i, "Письмо о ремонте дорог", "Смирнов", ctl=True, sup="Орлова") for i in range(6)]
    r = mod.suggest("О ремонте дорог.", ctl, "", TODAY)
    assert r["control"]["recommend"] and r["control"]["supervisor"] == "Орлова"


def test_find_deadlines():
    text = "Прошу представить до 15.10.2026, не позднее 1 ноября, в срок до 31.02.2026; письмо от 01.09.2026; до 01.01.2020"
    assert mod.find_deadlines(text, TODAY) == [date(2026, 10, 15), date(2026, 11, 1)]
    # Месяц без года, уже прошедший в этом году — следующий год.
    assert mod.find_deadlines("не позднее 10 января", TODAY) == [date(2027, 1, 10)]


def test_deadline_variants():
    hist = [item(i, "Письмо о ремонте дорог", "Смирнов", term=12) for i in range(3)]
    r = mod.suggest("О ремонте дорог.", hist, "", TODAY)
    assert r["deadline"]["typical_term_days"] == 12 and r["deadline"]["proposed"] == "2026-10-12"
    # Срок документа слишком близко — без запаса.
    r = mod.suggest("О ремонте дорог, в срок до 01.10.2026.", hist, "", TODAY)
    assert r["deadline"]["proposed"] == "2026-10-01" and "запаса нет" in r["deadline"]["basis"]
    hist = [item(1, "Письмо о ремонте дорог", "Смирнов", term=None)]
    assert mod.suggest("О ремонте дорог.", hist, "", TODAY)["deadline"]["proposed"] is None


def test_minus_workdays():
    assert mod.minus_workdays(date(2026, 10, 20), 3) == date(2026, 10, 15)
    assert mod.minus_workdays(date(2026, 10, 5), 1) == date(2026, 10, 2)


def test_compound_executor_and_clean_text():
    hist = [item(1, "Письмо о цифровых сервисах", "", parts=["Иванов", "Смирнов"],
                 text="Вы являетесь исполнителем. п.1 провести совещание")] * 2
    r = mod.suggest("О цифровых сервисах", hist, "", TODAY)
    assert r["assignee"]["name"] == "Иванов" and [c["name"] for c in r["co_assignees"]] == ["Смирнов"]
    assert "Провести совещание" in r["resolution_draft"]
    assert mod.clean_text("Вы являетесь соисполнителем. п.2 Выполнить") == "Выполнить"


def test_recency_breaks_ties():
    hist = [item(1, "Письмо о ремонте дорог", "Старый", created="2023-01-01"),
            item(2, "Письмо о ремонте дорог", "Новый", created="2026-09-01")]
    assert mod.suggest("О ремонте дорог", hist, "", TODAY)["assignee"]["name"] == "Новый"


def test_stems_and_stopwords():
    assert mod.stems("Прошу подготовить информацию о ремонте ДОРОГ") == ["ремонт", "дорог"]
    assert mod.stems("") == []


def test_bad_inputs():
    with pytest.raises(mod.InputError):
        mod.suggest("о", HISTORY, "", TODAY)
    for bad in ({"x": 1}, "s", 5):
        with pytest.raises(mod.InputError):
            mod.suggest("О ремонте дорог", bad, "", TODAY)
    r = mod.suggest("О ремонте дорог", [], "", TODAY)
    assert r["assignee"] is None and r["confidence"] == "низкая"
    r = mod.suggest("О ремонте дорог", {"items": [item(1, "дороги", "С")], "truncated": True, "total": 9}, "", TODAY)
    assert any("История неполная" in n for n in r["notes"])


def test_large_history_is_fast():
    topics = ["ремонт дорог", "прививочные кабинеты", "олимпиада школьников", "цифровые сервисы", "благоустройство дворов"]
    hist = [item(i, f"Письмо: {topics[i % 5]} номер {i}", f"И{i % 30}", created=f"2026-{1 + i % 9:02d}-01") for i in range(mod.MAX_HISTORY)]
    t0 = time.perf_counter()
    mod.suggest("О ремонте дорог и благоустройстве дворов " * 200, hist, "", TODAY)
    assert time.perf_counter() - t0 < 15


def test_markdown():
    text = (SKILL / "examples" / "incoming.txt").read_text(encoding="utf-8")
    md = mod.to_markdown(mod.suggest(text, HISTORY, TITLE, TODAY))
    assert md.startswith("# Проект резолюции") and "**Поставить на контроль**" in md and "05.09.2026" in md


def test_cli(tmp_path):
    inc, hist = SKILL / "examples" / "incoming.txt", SKILL / "examples" / "history.json"
    code, out, _ = run_cli(str(inc), "--history", str(hist), "--today", "2026-09-30")
    assert code == 0 and json.loads(out)["assignee"]["name"] == "Иванов И. И."
    code, out, _ = run_cli("-", "--history", str(hist), "--format", "md", "--title", TITLE, stdin=inc.read_bytes())
    assert code == 0 and "# Проект резолюции" in out
    code, _, err = run_cli(str(inc))
    assert code == 2 and "Ошибка аргументов" in err
    code, _, err = run_cli("-", "--history", "-")
    assert code == 2 and "stdin" in err
    code, _, err = run_cli(str(inc), "--history", str(tmp_path / "нет.json"))
    assert code == 2 and "не найден" in err
    bad = tmp_path / "b.json"
    bad.write_text("{", encoding="utf-8")
    code, _, err = run_cli(str(inc), "--history", str(bad))
    assert code == 2 and "не JSON" in err
    code, _, err = run_cli(str(inc), "--history", str(hist), "--top", "0")
    assert code == 2 and "--top" in err
    code, _, err = run_cli("-", "--history", str(hist), stdin=b"  ")
    assert code == 2 and "пустой" in err
