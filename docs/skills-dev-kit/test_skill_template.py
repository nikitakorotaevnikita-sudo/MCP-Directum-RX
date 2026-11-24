"""Шаблон тестов навыка. Скопируйте в <ПАПКА_ТЕСТОВ>/test_<имя_скрипта>.py и замените SKILL_DIR и SCRIPT_NAME.

Запуск: python -m pytest <ПАПКА_ТЕСТОВ>/test_<имя_скрипта>.py -q
"""

import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

# Путь к папке навыка: абсолютный или относительно папки этого теста.
SKILL_DIR = "<ПАПКА_НАВЫКОВ>/<имя-навыка>"
SCRIPT_NAME = "skill_script.py"    # имя скрипта в scripts/
SKILL = (Path(__file__).resolve().parent / SKILL_DIR).resolve()
SCRIPT = SKILL / "scripts" / SCRIPT_NAME
EXAMPLE = SKILL / "examples" / "example_input.json"

_spec = importlib.util.spec_from_file_location("skill_script", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["skill_script"] = mod
_spec.loader.exec_module(mod)

TODAY = date(2026, 11, 20)


def run_cli(*args, stdin=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


# --- логика -------------------------------------------------------------------

def test_counts_overdue_and_sorts_worst_first():
    data = {"items": [{"id": 1, "deadline": "2026-11-19"}, {"id": 2, "deadline": "2026-11-10"},
                      {"id": 3, "deadline": "2026-12-01"}]}
    result = mod.check(data, TODAY)
    assert result["overdue"] == 2
    assert [r["id"] for r in result["rows"]][:2] == [2, 1]


def test_only_overdue_filter():
    data = {"items": [{"id": 1, "deadline": "2026-11-10"}, {"id": 2, "deadline": "2026-12-01"}]}
    assert [r["id"] for r in mod.check(data, TODAY, only_overdue=True)["rows"]] == [1]


def test_missing_deadline_is_warning_not_error():
    result = mod.check({"items": [{"id": 1, "deadline": None}]}, TODAY)
    assert result["total"] == 0 and "нет срока" in result["warnings"][0]


def test_bad_input_raises_input_error():
    with pytest.raises(mod.InputError):
        mod.check({"items": "не список"}, TODAY)


# --- пример и командная строка -------------------------------------------------

def test_example_runs():
    code, out, err = run_cli(str(EXAMPLE), "--today", "2026-11-20")
    assert code == 0, err
    assert json.loads(out)["overdue"] == 1


def test_markdown_has_links():
    code, out, _ = run_cli(str(EXAMPLE), "--format", "md", "--today", "2026-11-20")
    assert code == 0 and "](https://rx.example.ru/card/1072)" in out


def test_stdin_and_exit_code_2_on_bad_json():
    code, _, err = run_cli("-", stdin=b"not json")
    assert code == 2 and "Ошибка входных данных" in err
