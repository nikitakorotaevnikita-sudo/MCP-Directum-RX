import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "docs" / "usecases" / "skills" / "og-repeat"
SCRIPT = SKILL / "scripts" / "find_repeats.py"

_spec = importlib.util.spec_from_file_location("find_repeats", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["find_repeats"] = mod
_spec.loader.exec_module(mod)


def run_cli(*args, stdin=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def req(id_, day, questions, applicant="Иванов Иван Иванович", **kw):
    base = {"id": id_, "registration_number": f"{id_}-ОГ", "registration_date": day, "applicant": applicant,
            "questions": [{"question": q, "code": c} for q, c in questions]}
    base.update(kw)
    return base


def by_id(result):
    return {r["id"]: r for r in result["requests"]}


def test_same_code_within_period_is_repeat():
    res = by_id(mod.find([req(1, "2026-01-10", [("Дороги", "0003")]), req(2, "2026-03-01", [("Дороги", "0003")])]))
    assert not res[1]["repeat_found"]
    assert res[2]["repeat_found"] and res[2]["earlier"][0]["id"] == 1 and res[2]["earlier"][0]["days_before"] == 50
    assert res[2]["flag_mismatch"] and "не отмечено" in res[2]["notes"][0]


def test_period_boundary():
    data = [req(1, "2026-01-01", [("Дороги", "0003")]), req(2, "2026-06-30", [("Дороги", "0003")])]
    assert by_id(mod.find(data, period_days=180))[2]["repeat_found"]
    assert not by_id(mod.find(data, period_days=179))[2]["repeat_found"]


def test_different_questions_are_not_repeat():
    res = by_id(mod.find([req(1, "2026-01-10", [("Дороги", "0003")]), req(2, "2026-02-01", [("Свет", "0005")])]))
    assert not res[2]["repeat_found"]


def test_name_match_without_code_and_normalization():
    data = [req(1, "2026-01-10", [("Вывоз  ТКО.", None)], applicant="Семёнов  Пётр"),
            req(2, "2026-02-01", [("вывоз тко", None)], applicant="семенов пётр", is_repeated=True)]
    res = by_id(mod.find(data))
    assert res[2]["repeat_found"] and not res[2]["flag_mismatch"]
    assert res[2]["shared_questions"][0]["code"] is None


def test_code_and_name_keys_do_not_mix():
    data = [req(1, "2026-01-10", [("Дороги", "0003")]), req(2, "2026-02-01", [("Дороги", None)])]
    assert not by_id(mod.find(data))[2]["repeat_found"]


def test_different_applicants_are_separate():
    data = [req(1, "2026-01-10", [("Дороги", "0003")], applicant="А"),
            req(2, "2026-02-01", [("Дороги", "0003")], applicant="Б")]
    res = mod.find(data)
    assert res["summary"]["applicants"] == 2 and res["summary"]["repeats_found"] == 0


def test_article_11_threshold():
    data = [req(1, "2026-01-10", [("Дороги", "0003")], answered=True),
            req(2, "2026-02-10", [("Дороги", "0003")], execution_state="исполнено"),
            req(3, "2026-03-10", [("Дороги", "0003")])]
    res = by_id(mod.find(data))
    assert res[3]["article_11_check"] and res[3]["shared_questions"][0]["earlier_answered"] == 2
    assert not res[2]["article_11_check"]
    assert not by_id(mod.find(data, answered_threshold=3))[3]["article_11_check"]
    assert by_id(mod.find(data, answered_threshold=1))[2]["article_11_check"]


def test_unanswered_earlier_does_not_trigger_article_11():
    data = [req(1, "2026-01-10", [("Дороги", "0003")]), req(2, "2026-02-10", [("Дороги", "0003")]),
            req(3, "2026-03-10", [("Дороги", "0003")])]
    assert not by_id(mod.find(data))[3]["article_11_check"]


def test_flag_without_found_repeat_and_missing_data_notes():
    data = [req(1, "2026-02-10", [("Дороги", "0003")], is_repeated=True), req(2, None, [("Дороги", "0003")]),
            req(3, "2026-03-01", [])]
    res = by_id(mod.find(data))
    assert res[1]["flag_mismatch"] and "не найдено" in res[1]["notes"][0]
    assert any("Нет даты" in n for n in res[2]["notes"]) and not res[2]["repeat_found"]
    assert any("нет вопросов" in n for n in res[3]["notes"])


def test_same_day_counts_and_order_is_stable():
    data = [req(2, "2026-02-10", [("Дороги", "0003")]), req(1, "2026-02-10", [("Дороги", "0003")])]
    res = by_id(mod.find(data))
    assert res[2]["repeat_found"] and not res[1]["repeat_found"]


def test_without_applicant_counted():
    res = mod.find([req(1, "2026-01-01", [("Дороги", "0003")], applicant=None)])
    assert res["summary"]["without_applicant"] == 1 and res["requests"] == []


def test_request_date_used_when_no_registration():
    data = [req(1, None, [("Дороги", "0003")], request_date="2026-01-10"), req(2, "2026-02-01", [("Дороги", "0003")])]
    assert by_id(mod.find(data))[2]["repeat_found"]


def test_bad_shapes():
    for bad in ({"foo": 1}, "x", [], {"items": ["x"]}):
        try:
            mod.find(bad)
        except mod.InputError:
            pass
        else:
            raise AssertionError(bad)


def test_large_single_applicant_is_fast():
    data = [req(i, f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}", [("Дороги", "0003" if i % 2 else "0005")])
            for i in range(2000)]
    t0 = time.perf_counter()
    mod.find(data)
    assert time.perf_counter() - t0 < 20


def test_example_matches_expected():
    data = json.loads((SKILL / "examples" / "applicant.json").read_text(encoding="utf-8"))
    expected = json.loads((SKILL / "examples" / "applicant.expected.json").read_text(encoding="utf-8"))
    assert mod.find(data) == expected


def test_cli(tmp_path):
    src = tmp_path / "a.json"
    src.write_text(json.dumps([req(1, "2026-01-10", [("Дороги", "0003")]), req(2, "2026-03-01", [("Дороги", "0003")])],
                              ensure_ascii=False), encoding="cp1251")
    code, out, _ = run_cli(str(src))
    assert code == 0 and json.loads(out)["summary"]["repeats_found"] == 1
    code, out, _ = run_cli(str(src), "--format", "md")
    assert code == 0 and "# Повторные обращения" in out
    code, _, err = run_cli(str(src), "--period-days", "0")
    assert code == 2 and "--period-days" in err
    code, _, err = run_cli(str(src), "--answered-threshold", "50")
    assert code == 2 and "--answered-threshold" in err
    code, _, err = run_cli("-", stdin=b"[")
    assert code == 2 and "не JSON" in err
    code, _, err = run_cli()
    assert code == 2 and "Ошибка аргументов" in err
