import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "docs" / "usecases" / "skills" / "kd-close"
SCRIPT = SKILL / "scripts" / "close_readiness.py"

_spec = importlib.util.spec_from_file_location("close_readiness", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["close_readiness"] = mod
_spec.loader.exec_module(mod)


def run_cli(*args, stdin=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def dossier(tree=None, history=None, acceptance=None, documents=None, **item):
    root = {"id": 1, "subject": "Поручение", "deadline": "2026-10-01", "actual_date": "2026-09-28", "status": "Completed",
            "execution_state_label": "исполнено", "url": "https://x/1"}
    root.update(item)
    return {"action_item": root,
            "tree": tree if tree is not None else [dict(root, report="Сделано")],
            "deadline_history": history or [], "acceptance": acceptance or [], "documents": documents or []}


def test_ready_simple():
    r = mod.check(dossier(acceptance=[{"action_item_id": 1, "status": "Completed", "result": "принято", "supervisor": "Орлова", "completed": "2026-09-29"}]))
    assert r["ready"] and r["blockers"] == [] and r["warnings"] == []
    assert "Работы приняты контролёром Орлова 29.09.2026." in r["facts"]
    assert "в срок" in r["draft_reason"] and r["draft_reason"].startswith("Снять с контроля в связи с исполнением.")


def test_open_children_block():
    tree = [{"id": 1, "status": "Completed", "report": "ok"},
            {"id": 2, "status": "InProcess", "assignee": "Петров", "execution_state_label": "на исполнении", "days_overdue": 3}]
    r = mod.check(dossier(tree=tree))
    assert not r["ready"] and r["draft_reason"] is None
    assert r["blockers"] == ["Не завершено поручение № 2 (Петров): на исполнении, просрочено на 3 дн."]


def test_root_on_acceptance_is_not_blocker():
    tree = [{"id": 1, "status": "InProcess", "execution_state_label": "на приёмке у контролёра", "report": "Отчёт"}]
    r = mod.check(dossier(tree=tree, status="InProcess", execution_state_label="на приёмке у контролёра",
                          acceptance=[{"action_item_id": 1, "status": "InProcess"}]))
    assert r["ready"] and "ждёт приёмки" in r["facts"][0]


def test_pending_extension_blocks_and_accepted_counted():
    history = [{"state": "на рассмотрении", "new_deadline": "2026-10-05", "action_item_id": 1},
               {"state": "одобрено", "shift_days": 4}, {"state": "одобрено", "shift_days": 6}, {"state": "отклонено", "shift_days": 9}]
    r = mod.check(dossier(history=history))
    assert r["blockers"] == ["Не рассмотрен запрос на продление до 05.10.2026 (поручение № 1)."]
    assert "Срок переносился 2 раз(а), суммарно на 10 дн." in r["facts"]


def test_warnings():
    tree = [{"id": 1, "status": "Completed", "report": " "}, {"id": 3, "status": "Aborted"}]
    r = mod.check(dossier(tree=tree, acceptance=[{"action_item_id": 1, "status": "Completed", "result": "на доработку"}]))
    assert r["ready"]
    joined = " ".join(r["warnings"])
    assert "без отчёта" in joined and "Прекращено поручений: 1" in joined and "на доработку" in joined


def test_late_execution_and_overdue_without_fact():
    r = mod.check(dossier(actual_date="2026-10-04"))
    assert any("с нарушением срока на 3 дн." in f for f in r["facts"])
    r = mod.check(dossier(actual_date=None, days_overdue=5))
    assert any("Срок нарушен на 5 дн." in w for w in r["warnings"])


def test_documents_only_count_no_names():
    r = mod.check(dossier(documents=[{"id": 1, "name": "Обращение Иванова И. И.", "is_source": False},
                                     {"id": 2, "name": "Основание", "is_source": True}]))
    assert "Документов во вложениях: 1." in r["facts"]
    assert "Иванова" not in json.dumps(r, ensure_ascii=False)


def test_empty_tree_uses_item():
    r = mod.check({"action_item": {"id": 5, "status": "InProcess", "execution_state_label": "на исполнении"}})
    assert not r["ready"]


def test_bad_input():
    for bad in ({}, [], "x", {"action_item": 5}):
        with pytest.raises(mod.InputError):
            mod.check(bad)


def test_example_matches_expected():
    data = json.loads((SKILL / "examples" / "dossier.json").read_text(encoding="utf-8"))
    expected = json.loads((SKILL / "examples" / "dossier.expected.json").read_text(encoding="utf-8"))
    assert mod.check(data) == expected


def test_cli(tmp_path):
    ex = SKILL / "examples" / "dossier.json"
    code, out, _ = run_cli(str(ex))
    assert code == 0 and json.loads(out)["ready"] is False
    code, out, _ = run_cli(str(ex), "--format", "md")
    assert code == 0 and "Не готово к снятию" in out
    code, _, err = run_cli(str(tmp_path / "нет.json"))
    assert code == 2 and "не найден" in err
    code, _, err = run_cli("-", stdin=b"")
    assert code == 2 and "пустые" in err
    code, _, err = run_cli("-", stdin=b"[1]")
    assert code == 2 and "досье" in err
    code, _, err = run_cli()
    assert code == 2 and "Ошибка аргументов" in err


def test_open_report_request_is_warning():
    d = dossier()
    d["report_requests"] = [{"status": "InProcess"}, {"status": "Completed"}]
    r = mod.check(d)
    assert r["ready"] and any("незакрытые запросы отчёта: 1" in w for w in r["warnings"])
