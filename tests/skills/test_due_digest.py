import importlib.util
import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "docs" / "usecases" / "skills"
SKILL = SKILLS / "og-control"
SCRIPT = SKILL / "scripts" / "due_digest.py"

_spec = importlib.util.spec_from_file_location("due_digest", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["due_digest"] = mod
_spec.loader.exec_module(mod)

TODAY = date(2026, 9, 29)


def run_cli(*args, stdin=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def req(id_, deadline, assignee="Иванов И. И.", **kw):
    base = {"id": id_, "registration_number": f"{id_}-ОГ", "registration_date": "2026-09-01", "deadline": deadline,
            "assignee": assignee, "questions": [{"question": "Дороги"}], "url": f"https://x/{id_}"}
    base.update(kw)
    return base


def test_buckets():
    cases = {"2026-09-10": "overdue_10_plus", "2026-09-19": "overdue_1_10", "2026-09-28": "overdue_1_10",
             "2026-09-29": "today", "2026-10-02": "in_1_3", "2026-10-06": "in_4_7", "2026-10-07": "later", None: "no_deadline"}
    rows = mod.digest([req(i, d) for i, d in enumerate(cases)], TODAY)["rows"]
    assert {r["deadline"]: r["bucket"] for r in rows} == cases
    # Граница: ровно 10 дней просрочки — ещё «1–10».
    assert mod.bucket_of(-10) == "overdue_1_10" and mod.bucket_of(-11) == "overdue_10_plus"


def test_not_in_work_skipped():
    data = [req(1, "2026-09-01", answered=True), req(2, "2026-09-01", transferred_to="Минздрав"),
            req(3, "2026-09-01", execution_state="прекращено"), req(4, "2026-09-01", answer_letter={"id": 1}),
            req(5, "2026-09-01", execution_state="Executed"), req(6, "2026-09-01")]
    res = mod.digest(data, TODAY)
    assert res["summary"]["in_work"] == 1 and res["summary"]["skipped_not_in_work"] == 5


def test_sorting_and_most_overdue():
    res = mod.digest([req(1, "2026-10-01"), req(2, "2026-09-01"), req(3, None), req(4, "2026-09-20")], TODAY, top=1)
    assert [r["id"] for r in res["rows"]] == [2, 4, 1, 3]
    assert [r["id"] for r in res["most_overdue"]] == [2]
    assert res["most_overdue"][0]["days_overdue"] == 28


def test_by_assignee():
    data = [req(1, "2026-09-01", "А"), req(2, "2026-09-25", "А"), req(3, "2026-10-01", "Б"), req(4, "2026-10-01", None),
            req(5, "2026-09-20", "Б")]
    res = mod.digest(data, TODAY)
    rows = {a["assignee"]: a for a in res["by_assignee"]}
    assert rows["А"] == {"assignee": "А", "in_work": 2, "overdue": 2, "due_7_days": 0, "max_days_overdue": 28}
    assert rows["Б"]["overdue"] == 1 and rows["Б"]["due_7_days"] == 1
    assert mod.NO_ASSIGNEE in rows
    assert [a["assignee"] for a in res["by_assignee"]][:2] == ["А", "Б"]


def test_summary_and_truncation_note():
    data = {"items": [req(1, "2026-09-29"), req(2, "2026-10-03", prolongations=[{"number": 1}])], "total": 5}
    res = mod.digest(data, TODAY)
    assert res["summary"] == {"in_work": 2, "skipped_not_in_work": 0, "overdue": 0, "due_today": 1, "due_7_days": 2,
                              "no_deadline": 0, "prolonged": 1}
    assert res["notes"] and "2 из 5" in res["notes"][0]
    assert mod.digest({"items": [req(1, "2026-09-29")], "total": 1}, TODAY)["notes"] == []


def test_empty_is_ok():
    res = mod.digest({"items": [], "total": 0}, TODAY)
    assert res["summary"]["in_work"] == 0
    assert "# Контроль сроков обращений" in mod.to_markdown(res)


def test_bad_shapes():
    for bad in ({"foo": 1}, "x", 5):
        try:
            mod.digest(bad, TODAY)
        except mod.InputError:
            pass
        else:
            raise AssertionError(bad)


def test_xlsx_spec_builds_with_excel_skill(tmp_path):
    openpyxl = __import__("pytest").importorskip("openpyxl")
    spec_path = SKILLS / "excel-report" / "scripts" / "build_xlsx.py"
    s = importlib.util.spec_from_file_location("build_xlsx_for_og", spec_path)
    build_xlsx = importlib.util.module_from_spec(s)
    s.loader.exec_module(build_xlsx)
    data = json.loads((SKILL / "examples" / "due.json").read_text(encoding="utf-8"))
    spec = mod.to_xlsx_spec(mod.digest(data, TODAY))
    out = build_xlsx.build(spec, str(tmp_path / "due.xlsx"))
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["Обращения в работе", "По исполнителям"]


def test_large_input_is_fast():
    data = [req(i, "2026-09-01" if i % 2 else "2026-10-10", f"Исп {i % 50}") for i in range(mod.MAX_REQUESTS)]
    t0 = time.perf_counter()
    mod.digest(data, TODAY)
    assert time.perf_counter() - t0 < 10


def test_example_matches_expected():
    data = json.loads((SKILL / "examples" / "due.json").read_text(encoding="utf-8"))
    expected = json.loads((SKILL / "examples" / "due.expected.json").read_text(encoding="utf-8"))
    assert mod.digest(data, TODAY) == expected


def test_cli(tmp_path):
    src = tmp_path / "a.json"
    src.write_text(json.dumps({"items": [req(1, "2026-09-01")]}, ensure_ascii=False), encoding="utf-8")
    code, out, _ = run_cli(str(src), "--today", "2026-09-29")
    assert code == 0 and json.loads(out)["summary"]["overdue"] == 1
    code, out, _ = run_cli(str(src), "--today", "2026-09-29", "--format", "md")
    assert code == 0 and "## Самые просроченные" in out
    code, out, _ = run_cli(str(src), "--format", "xlsx-spec")
    assert code == 0 and json.loads(out)["sheets"][0]["name"] == "Обращения в работе"
    code, _, err = run_cli(str(src), "--top", "0")
    assert code == 2 and "--top" in err
    code, _, err = run_cli(str(src), "--today", "x")
    assert code == 2 and "--today" in err
    code, _, err = run_cli("-", stdin=b"")
    assert code == 2 and "пустые" in err
    code, _, err = run_cli()
    assert code == 2 and "Ошибка аргументов" in err
