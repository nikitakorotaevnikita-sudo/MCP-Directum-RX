import importlib.util
import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "docs" / "usecases" / "skills" / "kd-monitor"
SCRIPT = SKILL / "scripts" / "risk_score.py"

_spec = importlib.util.spec_from_file_location("risk_score", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["risk_score"] = mod
_spec.loader.exec_module(mod)

TODAY = date(2026, 9, 29)


def run_cli(*args, stdin=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def item(id_=1, deadline="2026-10-20", created="2026-09-25", assignee="Иванов", **kw):
    base = {"id": id_, "subject": f"Поручение {id_}", "deadline": deadline, "created": created, "status": "InProcess",
            "execution_state_label": "на исполнении", "assignee": assignee, "url": f"https://x/{id_}"}
    base.update(kw)
    return base


def dossier(id_=1, **summary):
    return {"action_item": {"id": id_}, "summary": summary, "tree": [], "deadline_history": [], "acceptance": [],
            "correspondence": [{"created": "2026-09-28"}]}


def one(it, dossiers=None):
    return mod.score_all([it], TODAY, dossiers)["items"][0]


def test_groups_basic():
    assert one(item(deadline="2026-09-20"))["group"] == "overdue"
    assert one(item(deadline="2026-09-20"))["days_overdue"] == 9
    assert one(item(execution_state_label="на приёмке у контролёра", deadline="2026-09-20"))["group"] == "on_acceptance"
    assert one(item(execution_state="OnControl", execution_state_label=None))["group"] == "on_acceptance"
    assert one(item(deadline=None))["group"] == "no_deadline"
    assert one(item(status="Completed", deadline="2026-09-01"))["group"] == "normal"
    assert one(item())["group"] == "normal" and one(item())["score"] == 0


def test_deadline_proximity_and_elapsed_share():
    r = one(item(deadline="2026-09-29", created="2026-09-28"))
    assert r["group"] == "high" and "Срок сегодня." in r["reasons"]
    assert one(item(deadline="2026-09-30", created="2026-09-29"))["reasons"][0] == "Срок завтра."
    r = one(item(deadline="2026-10-02", created="2026-09-28"))
    assert r["group"] == "medium" and r["reasons"] == ["До срока 3 дн."]
    r = one(item(deadline="2026-10-10", created="2026-08-01"))
    assert r["group"] == "medium" and r["reasons"][0].startswith("Прошло 84 %")


def test_load_signal():
    items = [item(i, deadline="2026-10-05", created="2026-09-28") for i in range(5)]
    res = mod.score_all(items, TODAY)["items"]
    assert all("поручений на контроле" in " ".join(r["reasons"]) for r in res)
    assert all(r["group"] == "medium" for r in res)


def test_dossier_signals():
    r = one(item(), {"dossiers": [dossier(extensions_accepted=2)]})
    assert r["group"] == "medium" and r["score"] == 2 and r["dossier_used"]
    r = one(item(), [dossier(extensions_accepted=1, extensions_pending=1, nodes_overdue=1)])
    assert r["group"] == "high" and r["score"] == 4
    stale = dict(dossier(), correspondence=[{"created": "2026-09-01"}])
    assert "Нет движения" in " ".join(one(item(created="2026-09-01"), stale)["reasons"])
    empty = dict(dossier(), correspondence=[])
    assert "Нет следов движения" in " ".join(one(item(created="2026-09-01"), empty)["reasons"])
    # Свежее поручение без следов — не «нет движения».
    assert one(item(created="2026-09-25"), empty)["score"] == 0


def test_activity_sources():
    d = {"correspondence": [], "deadline_history": [{"requested": "2026-09-20"}], "acceptance": [{"created": "2026-09-22"}],
         "tree": [{"level": 0, "created": "2026-01-01"}, {"level": 1, "created": "2026-09-27", "actual_date": None}]}
    assert mod._last_activity(d) == date(2026, 9, 27)


def test_sorting_and_bottlenecks():
    items = [item(1), item(2, deadline="2026-09-28", assignee="Петров"), item(3, deadline="2026-09-10", assignee="Петров"),
             item(4, deadline="2026-09-29", created="2026-09-28", assignee="Сидоров")]
    res = mod.score_all(items, TODAY)
    assert [r["id"] for r in res["items"]] == [3, 2, 4, 1]
    assert [b["assignee"] for b in res["bottlenecks"]] == ["Петров", "Сидоров"]
    assert {g["key"]: g["count"] for g in res["groups"]}["overdue"] == 2


def test_notes():
    res = mod.score_all({"items": [item(1), item(2)], "total": 10}, TODAY, [dossier(1)])
    assert any("10 поручений" in n for n in res["notes"]) and any("1 из 2" in n for n in res["notes"])


def test_bad_inputs():
    for bad in ({"x": 1}, "s", 5):
        try:
            mod.score_all(bad, TODAY)
        except mod.InputError:
            pass
        else:
            raise AssertionError(bad)
    try:
        mod.score_all([item()], TODAY, "строка")
    except mod.InputError:
        pass
    else:
        raise AssertionError("dossiers")
    assert mod.score_all([{"no": "id"}, item()], TODAY)["items"][0]["id"] == 1


def test_weird_values():
    for it in (item(deadline="мусор"), item(created=None), item(created="2026-12-01"), item(deadline="29.09.2026")):
        one(it)
    assert one(item(deadline="29.09.2026", created="2026-09-28"))["group"] == "high"


def test_large_is_fast():
    items = [item(i, deadline=f"2026-10-{1 + i % 28:02d}", assignee=f"И{i % 40}") for i in range(mod.MAX_ITEMS)]
    t0 = time.perf_counter()
    mod.score_all(items, TODAY, [dossier(i) for i in range(2000)])
    assert time.perf_counter() - t0 < 10


def test_example_matches_expected():
    reg = json.loads((SKILL / "examples" / "registry.json").read_text(encoding="utf-8"))
    ds = json.loads((SKILL / "examples" / "dossiers.json").read_text(encoding="utf-8"))
    expected = json.loads((SKILL / "examples" / "registry.expected.json").read_text(encoding="utf-8"))
    assert mod.score_all(reg, TODAY, ds) == expected


def test_markdown():
    reg = json.loads((SKILL / "examples" / "registry.json").read_text(encoding="utf-8"))
    md = mod.to_markdown(mod.score_all(reg, TODAY))
    assert "## Просрочено (1)" in md and "## Ждёт приёмки (1)" in md and "## Узкие места" in md


def test_cli(tmp_path):
    reg = SKILL / "examples" / "registry.json"
    code, out, _ = run_cli(str(reg), "--today", "2026-09-29")
    assert code == 0 and json.loads(out)["today"] == "2026-09-29"
    code, out, _ = run_cli(str(reg), "--dossiers", str(SKILL / "examples" / "dossiers.json"), "--format", "md", "--today", "2026-09-29")
    assert code == 0 and "Сорвётся (2)" in out
    code, _, err = run_cli("-", "--dossiers", "-")
    assert code == 2 and "stdin" in err
    code, _, err = run_cli(str(reg), "--today", "x")
    assert code == 2 and "--today" in err
    code, _, err = run_cli(str(reg), "--dossiers", str(tmp_path / "нет.json"))
    assert code == 2 and "не найден" in err
    code, _, err = run_cli("-", stdin=b"{")
    assert code == 2 and "не JSON" in err
    code, _, err = run_cli()
    assert code == 2 and "Ошибка аргументов" in err
