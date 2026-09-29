import importlib.util
import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "docs" / "usecases" / "skills" / "og-terms"
SCRIPT = SKILL / "scripts" / "check_terms.py"

_spec = importlib.util.spec_from_file_location("check_terms", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["check_terms"] = mod
_spec.loader.exec_module(mod)

TODAY = date(2026, 9, 29)
# Для проверок регистрации и сроков — дата, когда базовое обращение ещё далеко от срока.
EARLY = date(2026, 9, 10)


def run_cli(*args, stdin=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def req(**kw):
    base = {"id": 1, "registration_number": "1-ОГ", "request_date": "2026-09-01", "registration_date": "2026-09-01",
            "deadline": "2026-10-01", "initial_deadline": "2026-10-01", "prolongations": [], "questions": []}
    base.update(kw)
    return base


def codes(r, today=EARLY):
    return [i["code"] for i in mod.check_request(r, today)["issues"]]


def test_parse_day_formats():
    assert mod.parse_day("2026-09-01") == date(2026, 9, 1)
    assert mod.parse_day("2026-09-01T00:00:00+04:00") == date(2026, 9, 1)
    assert mod.parse_day("2026-09-01T00:00:00Z") == date(2026, 9, 1)
    assert mod.parse_day("01.09.2026") == date(2026, 9, 1)
    for bad in ("", None, "вчера", "2026-13-01", 5, "31.02.2026"):
        assert mod.parse_day(bad) is None


def test_working_days_between():
    # Пт 04.09 → Пн 07.09: один рабочий день.
    assert mod.working_days_between(date(2026, 9, 4), date(2026, 9, 7)) == 1
    assert mod.working_days_between(date(2026, 9, 1), date(2026, 9, 1)) == 0
    assert mod.working_days_between(date(2026, 9, 7), date(2026, 9, 1)) == 0
    assert mod.working_days_between(date(2026, 8, 25), date(2026, 9, 1)) == 5
    assert mod.working_days_between(date(2026, 9, 1), date(2026, 9, 29)) == 20


def test_clean_request_has_only_expected_issues():
    assert codes(req()) == []


def test_registration_rules():
    # Пятница → вторник: 4 календарных, 2 рабочих — предупреждение, не нарушение.
    assert codes(req(request_date="2026-09-04", registration_date="2026-09-08")) == ["registration_late_calendar"]
    assert codes(req(request_date="2026-08-25", registration_date="2026-09-01")) == ["registration_late"]
    assert codes(req(request_date="2026-09-02", registration_date="2026-09-01")) == ["registration_before_receipt"]
    not_reg = req(registration_date=None, request_date="2026-09-21", deadline=None, initial_deadline=None)
    assert codes(not_reg, TODAY) == ["not_registered"]
    assert mod.check_request(not_reg, TODAY)["issues"][0]["severity"] == "violation"
    fresh = req(registration_date=None, request_date="2026-09-28", deadline=None, initial_deadline=None)
    assert mod.check_request(fresh, TODAY)["issues"][0]["severity"] == "info"


def test_review_and_prolongation_limits():
    assert "initial_deadline_over_30" in codes(req(initial_deadline="2026-10-05", deadline="2026-10-05"))
    many = req(initial_deadline="2026-10-01", deadline="2026-10-20",
               prolongations=[{"number": 1, "reason": "a"}, {"number": 2, "reason": "b"}])
    assert "multiple_prolongations" in codes(many)
    over = req(initial_deadline="2026-10-01", deadline="2026-11-05", prolongations=[{"number": 1, "reason": "a"}])
    assert set(codes(over)) >= {"prolongation_over_30", "total_over_60"}
    silent = req(initial_deadline="2026-10-01", deadline="2026-10-20")
    assert "prolongation_not_recorded" in codes(silent)
    no_reason = req(initial_deadline="2026-10-01", deadline="2026-10-20", prolongations=[{"number": 1, "reason": " "}])
    assert "prolongation_without_reason" in codes(no_reason)
    ok = req(initial_deadline="2026-10-01", deadline="2026-10-31", prolongations=[{"number": 1, "reason": "запрос"}])
    assert codes(ok) == []


def test_initial_deadline_falls_back_to_deadline():
    r = mod.check_request(req(initial_deadline=None), TODAY)
    assert r["initial_deadline"] == "2026-10-01" and r["metrics"]["review_term_days"] == 30


def test_in_work_deadlines():
    def w(**kw):
        return req(request_date="2026-09-15", registration_date="2026-09-15", **kw)

    assert codes(w(deadline="2026-09-20", initial_deadline="2026-09-20"), TODAY) == ["overdue"]
    r = mod.check_request(w(deadline="2026-09-20", initial_deadline="2026-09-20"), TODAY)
    assert r["metrics"]["days_overdue"] == 9 and r["metrics"]["days_left"] == -9
    assert codes(w(deadline="2026-09-29", initial_deadline="2026-09-29"), TODAY) == ["due_today", "prolongation_possible"]
    assert codes(w(deadline="2026-10-03", initial_deadline="2026-10-03"), TODAY) == ["due_soon", "prolongation_possible"]
    assert codes(w(deadline="2026-10-03", initial_deadline="2026-10-03"), TODAY) == codes(
        w(deadline="03.10.2026", initial_deadline="03.10.2026"), TODAY)
    assert codes(w(deadline=None, initial_deadline=None), TODAY) == ["no_deadline"]
    # Уже продлённое — подсказку о продлении не даём.
    prolonged = w(deadline="2026-10-03", initial_deadline="2026-09-20", prolongations=[{"number": 1, "reason": "x"}])
    assert "prolongation_possible" not in codes(prolonged, TODAY)


def test_soon_days_window():
    r = req(request_date="2026-09-15", registration_date="2026-09-15", deadline="2026-10-09", initial_deadline="2026-10-09")
    assert codes(r, TODAY) == []
    assert [i["code"] for i in mod.check_request(r, TODAY, soon_days=10)["issues"]] == ["due_soon", "prolongation_possible"]


def test_statuses():
    assert mod.check_request(req(transferred_to="Минздрав"), TODAY)["status"] == "перенаправлено"
    assert mod.check_request(req(execution_state="прекращено", answered=True), TODAY)["status"] == "прекращено"
    assert mod.check_request(req(execution_state="Aborted"), TODAY)["status"] == "прекращено"
    assert mod.check_request(req(answer_letter={"registration_date": "2026-09-10"}), TODAY)["status"] == "рассмотрено"
    assert mod.check_request(req(execution_state="исполнено"), TODAY)["status"] == "рассмотрено"
    # Перенаправленное и просроченное — не «просрочено в работе».
    assert codes(req(transferred_to="Минздрав", deadline="2026-09-01")) == []


def test_answered_late_uses_letter_date():
    r = req(deadline="2026-09-10", initial_deadline="2026-09-10", answered=True,
            answer_letter={"registration_date": "2026-09-15T00:00:00+04:00"})
    res = mod.check_request(r, TODAY)
    assert [i["code"] for i in res["issues"]] == ["answered_late"]
    assert res["metrics"]["answered_late_days"] == 5 and res["response_date"] == "2026-09-15"
    on_time = req(deadline="2026-09-10", initial_deadline="2026-09-10", answered=True, response_date="2026-09-10")
    assert codes(on_time) == []


def test_questions_checks():
    r = req(answered=True, response_date="2026-09-20", questions=[
        {"question": "Дороги", "code": "0003", "review_result": None, "review_result_label": None},
        {"question": "Свет", "code": None, "review_result": "Explained"},
        {"question": "", "code": None}, "мусор"])
    assert codes(r) == ["review_result_missing", "question_not_classified"]


def test_issue_order_by_severity():
    r = req(request_date="2026-09-04", registration_date="2026-09-08", deadline="2026-09-20",
            initial_deadline="2026-09-20", questions=[{"question": "X"}])
    sev = [i["severity"] for i in mod.check_request(r, TODAY)["issues"]]
    assert sev == sorted(sev, key=lambda s: mod.SEVERITY_ORDER[s])


def test_extract_shapes():
    one = req()
    assert len(mod.extract_requests({"items": [one, "x"]})) == 1
    assert len(mod.extract_requests([one])) == 1
    assert len(mod.extract_requests(one)) == 1
    for bad in ({"foo": 1}, "строка", [], {"items": []}, [1, 2]):
        try:
            mod.extract_requests(bad)
        except mod.InputError:
            pass
        else:
            raise AssertionError(bad)


def test_summary_counts():
    fresh = req(request_date="2026-09-25", registration_date="2026-09-25", deadline="2026-10-20", initial_deadline="2026-10-20")
    res = mod.check([fresh, req(id=2, deadline="2026-09-20", initial_deadline="2026-09-20")], TODAY)
    assert res["summary"]["requests"] == 2 and res["summary"]["overdue"] == 1
    assert res["summary"]["with_violations"] == 1 and res["summary"]["by_code"] == {"overdue": 1}


def test_weird_values_do_not_crash():
    for r in (req(deadline="мусор"), req(prolongations=["x", None, 5]), req(questions=None), req(answer_letter="x"),
              {"id": 9}, req(registration_date=None, request_date=None)):
        mod.check_request(r, TODAY)


def test_large_input_is_fast():
    data = [req(id=i) for i in range(mod.MAX_REQUESTS)]
    t0 = time.perf_counter()
    mod.check(data, TODAY)
    assert time.perf_counter() - t0 < 10


def test_example_matches_expected():
    data = json.loads((SKILL / "examples" / "requests.json").read_text(encoding="utf-8"))
    expected = json.loads((SKILL / "examples" / "requests.expected.json").read_text(encoding="utf-8"))
    assert mod.check(data, TODAY) == expected


def test_markdown_escapes_and_links():
    md = mod.to_markdown(mod.check([req(registration_number="1|ОГ", url="https://x/1", deadline="2026-09-20",
                                        initial_deadline="2026-09-20")], TODAY))
    assert "[1\\|ОГ](https://x/1)" in md and "нарушение" in md


def test_cli(tmp_path):
    src = tmp_path / "a.json"
    src.write_text(json.dumps({"items": [req(deadline="2026-09-20", initial_deadline="2026-09-20")]}, ensure_ascii=False),
                   encoding="utf-8")
    code, out, _ = run_cli(str(src), "--today", "2026-09-29")
    assert code == 0 and json.loads(out)["summary"]["overdue"] == 1
    code, out, _ = run_cli(str(src), "--today", "29.09.2026", "--format", "md")
    assert code == 0 and "# Проверка сроков обращений" in out
    code, _, err = run_cli(str(src), "--today", "завтра")
    assert code == 2 and "--today" in err
    code, _, err = run_cli(str(src), "--soon-days", "99")
    assert code == 2 and "--soon-days" in err
    code, _, err = run_cli(str(tmp_path / "нет.json"))
    assert code == 2 and "не найден" in err
    code, _, err = run_cli("-", stdin=b"{not json")
    assert code == 2 and "не JSON" in err
    code, _, err = run_cli("-", stdin=b"   ")
    assert code == 2 and "пустые" in err
    code, _, err = run_cli()
    assert code == 2 and "Ошибка аргументов" in err
