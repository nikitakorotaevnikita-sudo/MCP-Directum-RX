import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "docs" / "usecases" / "skills" / "pa-compare"
SCRIPT = SKILL / "scripts" / "compare_texts.py"

_spec = importlib.util.spec_from_file_location("compare_texts", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["compare_texts"] = mod
_spec.loader.exec_module(mod)

OLD = ("Преамбула акта.\n"
       "1. Утвердить Порядок предоставления субсидий организациям.\n"
       "2. Обеспечить финансирование в пределах бюджетных ассигнований.\n"
       "3. Признать утратившим силу постановление от 01.02.2021 № 45-п.\n"
       "4. Постановление вступает в силу со дня официального опубликования.\n")
NEW = ("Преамбула акта.\n"
       "1. Утвердить Порядок предоставления субсидий организациям.\n"
       "2. Обеспечить финансирование в пределах лимитов бюджетных обязательств.\n"
       "3. Постановление вступает в силу со дня официального опубликования.\n"
       "4. Контроль возложить на министра.\n")


def run_cli(*args, stdin=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def test_units_split():
    text = ("Преамбула.\n1. Пункт.\n1.1. Подпункт\nпродолжение.\n2. Пункт:\n1) раз;\nа) аа.\n"
            "Приложение № 1\nк постановлению\n1. Прил.\n")
    units, numbered = mod.split_units(text)
    assert numbered
    assert [u.key for u in units] == ["преамбула", "п. 1", "п. 1.1", "п. 2", "п. 2, пп. 1)", "п. 2, пп. 1), пп. а)",
                                      "Приложение № 1", "Приложение № 1, п. 1"]
    assert units[2].text == "Подпункт\nпродолжение."


def test_added_removed_changed_renumbered():
    res = mod.compare(OLD, NEW)
    assert res["mode"] == "points"
    assert res["summary"] == {"added": 1, "removed": 1, "changed": 1, "unchanged": 2, "renumbered": 1}
    assert res["removed"] == [{"unit": "п. 3", "text": "Признать утратившим силу постановление от 01.02.2021 № 45-п."}]
    assert res["added"] == [{"unit": "п. 4", "text": "Контроль возложить на министра."}]
    assert res["renumbered"] == [{"old_unit": "п. 4", "new_unit": "п. 3", "similarity": 1.0}]
    ch = res["changed"][0]
    assert ch["unit"] == "п. 2"
    assert 0 < ch["similarity"] < 1
    ops = [(d["op"], d["before"], d["after"]) for d in ch["diff"]]
    assert ("insert", "", "лимитов") in ops
    assert ("replace", "ассигнований", "обязательств") in ops


def test_renumbered_with_small_edit():
    old = "1. А.\n2. Текст очень длинного пункта про порядок предоставления субсидий организациям края.\n"
    new = "1. Б совсем иное.\n2. Вставка.\n3. Текст очень длинного пункта про порядок предоставления субсидий организациям края!\n"
    res = mod.compare(old, new)
    ren = res["renumbered"]
    assert len(ren) == 1 and ren[0]["old_unit"] == "п. 2" and ren[0]["new_unit"] == "п. 3"
    assert 0.9 <= ren[0]["similarity"] < 1 and ren[0]["diff"]


def test_ignores_whitespace_quotes_dashes_nbsp():
    old = "1. Закон «О порядке» — действует.\n2. Второй   пункт.\n"
    new = "1. Закон \"О порядке\" - действует.\n2. Второй пункт.\n"
    res = mod.compare(old, new)
    assert res["summary"]["changed"] == 0 and res["summary"]["unchanged"] == 2


def test_original_text_shown():
    res = mod.compare("1. Закон «О порядке» действует.\n", "1. Закон \"О порядке\" не действует.\n")
    ch = res["changed"][0]
    assert ch["before"] == "Закон «О порядке» действует."
    assert ch["after"] == "Закон \"О порядке\" не действует."
    assert {"op": "insert", "before": "", "after": "не"} in ch["diff"]


def test_context_collapse():
    words = " ".join(f"слово{i}" for i in range(40))
    res = mod.compare(f"1. {words} старое.\n", f"1. {words} новое.\n", context=3)
    first = res["changed"][0]["diff"][0]
    assert first["op"] == "equal" and first["before"].startswith("…")
    assert first["before"] == "… слово37 слово38 слово39"
    res = mod.compare(f"1. {words} старое.\n", f"1. {words} новое.\n", context=100)
    assert "…" not in res["changed"][0]["diff"][0]["before"]


def test_paragraph_mode_without_numbering():
    old = "Первый абзац текста.\n\nВторой абзац про субсидии.\n\nТретий абзац."
    new = "Первый абзац текста.\n\nВторой абзац про гранты.\n\nТретий абзац.\n\nЧетвёртый абзац."
    res = mod.compare(old, new)
    assert res["mode"] == "paragraphs"
    assert res["summary"] == {"added": 1, "removed": 0, "changed": 1, "unchanged": 2, "renumbered": 0}
    assert res["added"][0]["text"] == "Четвёртый абзац."


def test_appendix_units():
    old = "1. Утвердить.\nПриложение № 1\nк постановлению\n1. Раз.\n2. Два.\n"
    new = "1. Утвердить.\nПриложение № 1\nк постановлению\n1. Раз.\n2. Два и три.\nПриложение № 2\nк постановлению\nФорма.\n"
    res = mod.compare(old, new)
    assert [c["unit"] for c in res["changed"]] == ["Приложение № 1, п. 2"]
    assert [a["unit"] for a in res["added"]] == ["Приложение № 2"]


def test_identical_and_weird_inputs():
    res = mod.compare(OLD, OLD)
    assert res["summary"]["changed"] == res["summary"]["added"] == res["summary"]["removed"] == 0
    for a, b in (("", ""), ("\n\n", "1. А."), ("1.\n1.\n1.", "1. А\n1. А"), ("а)\nб)", "x" * 10000),
                 (" \t", "1. " + "слово " * 20000)):
        mod.compare(a, b)


def test_large_input_is_fast():
    parts = [f"{i}. Пункт номер {i} о порядке предоставления субсидии организации {i % 7} края в размере {i} рублей "
             + "текст " * 50 for i in range(1, 2600)]
    old = "\n".join(parts)
    new_parts = parts[:1000] + parts[1001:]
    new_parts[10] = new_parts[10].replace("субсидии", "гранта")
    new = "\n".join(new_parts)
    assert len(old) >= 1_000_000
    t0 = time.perf_counter()
    res = mod.compare(old, new)
    assert time.perf_counter() - t0 < 10
    assert res["summary"]["changed"] >= 1
    # Худший случай: все пункты сдвинуты и чуть изменены — точных совпадений нет.
    bodies = [p.split(". ", 1)[1].replace("рублей", "руб.", 1) for p in parts]
    shifted = "\n".join(["1. Новый первый пункт."] + [f"{k}. {b}" for k, b in enumerate(bodies, 2)])
    t0 = time.perf_counter()
    res = mod.compare(old, shifted)
    assert time.perf_counter() - t0 < 10


def test_example_matches_expected():
    old = (SKILL / "examples" / "old.txt").read_text(encoding="utf-8")
    new = (SKILL / "examples" / "new.txt").read_text(encoding="utf-8")
    expected = json.loads((SKILL / "examples" / "expected.json").read_text(encoding="utf-8"))
    assert mod.compare(old, new) == expected


def test_cli(tmp_path):
    a, b = tmp_path / "old.txt", tmp_path / "new.txt"
    a.write_bytes(OLD.encode("cp1251"))
    b.write_text(NEW, encoding="utf-8-sig")
    code, out, _ = run_cli(str(a), str(b))
    assert code == 0 and json.loads(out)["summary"]["added"] == 1
    code, out, _ = run_cli(str(a), str(b), "--format", "md", "--context", "2")
    assert code == 0
    for header in ("# Сравнение редакций", "Итого:", "## Добавлено", "## Исключено", "## Изменено",
                   "## Перенумеровано", "**лимитов**", "~~**ассигнований**~~"):
        assert header in out
    assert out.index("Итого:") < out.index("## Добавлено")
    code, out, _ = run_cli("-", str(b), stdin=OLD.encode("utf-8"))
    assert code == 0 and json.loads(out)["summary"]["removed"] == 1
    code, _, err = run_cli(str(a), str(tmp_path / "нет.txt"))
    assert code == 2 and "не найден" in err
    code, _, err = run_cli("-", str(b), stdin=b"")
    assert code == 2 and "пустой" in err
    code, _, err = run_cli(str(a))
    assert code == 2 and "Ошибка аргументов" in err
    code, _, err = run_cli(str(a), str(b), "--context", "-1")
    assert code == 2
