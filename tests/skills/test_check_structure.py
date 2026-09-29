import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "docs" / "usecases" / "skills" / "pa-normcontrol"
SCRIPT = SKILL / "scripts" / "check_structure.py"

_spec = importlib.util.spec_from_file_location("check_structure", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["check_structure"] = mod
_spec.loader.exec_module(mod)

ENTRY = "Настоящее постановление вступает в силу со дня его официального опубликования."


def run_cli(*args, stdin=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def issues(text, type_=None):
    found = mod.check(text)["issues"]
    return [i for i in found if type_ is None or i["type"] == type_]


def test_points_and_levels():
    text = "Преамбула.\n1. Первый.\n1.1. Подпункт.\n1.1.1. Ещё.\n2. Второй:\n1) раз;\n2) два:\nа) аа;\nб) бб.\n" + ENTRY
    res = mod.check(text)
    nums = [(p["number"], p["line"]) for p in res["structure"]["points"]]
    assert nums == [("1", 2), ("1.1", 3), ("1.1.1", 4), ("2", 5), ("1)", 6), ("2)", 7), ("а)", 8), ("б)", 9)]
    assert res["structure"]["levels"] == {"1": 2, "2": 3, "3": 3}
    assert issues(text) == []


def test_numbering_gap_duplicate_restart():
    text = "1. А.\n2. Б.\n4. Г.\n4. Д.\n1. Е.\n" + ENTRY
    kinds = [(i["type"], i["line"], i["severity"]) for i in issues(text) if i["type"].startswith("numbering")]
    assert kinds == [("numbering_gap", 3, "error"), ("numbering_duplicate", 4, "error"),
                     ("numbering_restart", 5, "warning")]
    assert "пропущено 3" in issues(text, "numbering_gap")[0]["detail"]
    assert issues(text, "numbering_gap")[0]["type_label"] == "Пропуск в нумерации"


def test_letter_and_paren_gaps():
    text = "1. Пункт:\nа) раз;\nв) три;\n2. Пункт:\n1) раз;\n3) три.\n" + ENTRY
    gaps = issues(text, "numbering_gap")
    assert [g["line"] for g in gaps] == [3, 6]


def test_subpoints_restart_under_new_parent_is_ok():
    text = "1. А:\n1) раз;\n2) два.\n2. Б:\n1) раз;\n2) два.\n" + ENTRY
    assert issues(text) == []


def test_appendix_numbering_restarts_without_issue():
    text = "1. Утвердить Порядок согласно приложению № 1.\n2. " + ENTRY + "\n\nПриложение № 1\nк постановлению\n1. Раз.\n2. Два.\n"
    assert issues(text) == []


def test_dates_are_not_points():
    text = "01.02.2026 г. проведено\n2026 года\n1. Текст.\n" + ENTRY
    assert [p["number"] for p in mod.check(text)["structure"]["points"]] == ["1"]


def test_broken_references():
    text = ("1. Текст.\n2. Текст:\nа) раз;\nб) два.\n"
            "3. Согласно пункту 5, пунктам 1–6, подпункту «в» пункта 2, подпункту 2.4, абзацу второму пункта 7.\n"
            "4. Согласно пунктам 1 и 2, подпункту «а» пункта 2, пункту 3 настоящего постановления.\n" + ENTRY)
    found = issues(text, "broken_reference")
    assert [i["line"] for i in found] == [5, 5, 5, 5, 5]
    details = " ".join(i["detail"] for i in found)
    assert "пункт 5, пункт 6" in found[1]["detail"]
    for piece in ("пункт 5", "подпункт «в» пункта 2", "подпункт 2.4", "пункт 7"):
        assert piece in details


def test_references_to_other_acts_are_ignored():
    text = ("1. В соответствии с пунктом 5 статьи 12 Федерального закона от 02.05.2006 № 59-ФЗ,\n"
            "пунктом 8 Правил, утвержденных постановлением Правительства РФ, пунктом 3 части 1 статьи 7,\n"
            "пунктом 12 приказа Минфина России.\n2. " + ENTRY)
    assert issues(text, "broken_reference") == []


def test_missing_and_unused_appendices():
    text = ("1. Утвердить Порядок согласно приложению № 1.\n2. Утвердить форму (приложение № 3).\n"
            "3. Применять приложение № 5 к приказу Минфина России.\n4. " + ENTRY + "\n"
            "Приложение № 1\nк постановлению\nТекст.\nПРИЛОЖЕНИЕ 2\nк постановлению\nФорма.\n")
    res = mod.check(text)
    found = [(i["severity"], i["line"], i["detail"]) for i in res["issues"] if i["type"] == "missing_appendix"]
    assert found[0][:2] == ("error", 2) and "№ 3" in found[0][2]
    assert found[1][:2] == ("info", 8) and "Приложение № 2" in found[1][2]
    assert len(found) == 2
    assert res["structure"]["appendices_declared"] == ["1", "2"]
    assert res["structure"]["appendices_referenced"] == ["1", "3"]


def test_vague_terms():
    text = ("1. Своевременно представить в разумный срок, при необходимости, надлежащим образом, "
            "в установленном порядке в соответствующий орган иные документы и т. д., как правило, "
            "по возможности, в кратчайшие сроки и незамедлительно.\n" + ENTRY)
    found = issues(text, "vague_term")
    assert len(found) == 12
    assert [i["severity"] for i in found].count("info") == 1
    assert issues("1. Несвоевременность выявлена.\n" + ENTRY, "vague_term") == []


def test_no_entry_into_force():
    found = issues("1. Утвердить Порядок.\n", "no_entry_into_force")
    assert len(found) == 1 and found[0]["line"] is None and found[0]["severity"] == "warning"
    assert issues("1. Постановление вступает в законную силу с момента подписания.", "no_entry_into_force") == []


def test_placeholders():
    text = ("от ____________ № ________\n1. Срок до «__» ______ 20__ г.\n2. Дата XX.XX.XXXX.\n"
            "3. Орган [наименование].\n4. Лицо <ФИО> и … далее.\n5. Сноска <*> допустима.\n" + ENTRY)
    lines = [i["line"] for i in issues(text, "empty_placeholder")]
    assert sorted(set(lines)) == [2, 3, 4, 5]
    assert len(lines) == 5  # в строке 5 два разных шаблона: <ФИО> и «…»
    assert all(i["severity"] == "error" for i in issues(text, "empty_placeholder"))


def test_repeated_words_and_dates():
    text = "1. Направить в в министерство на\nна рассмотрение 1 1 раз до 2026-01-05, 5/1/2026, 5.1.2026 и 05.01.2026.\n" + ENTRY
    rep = issues(text, "repeated_word")
    assert [(i["line"], i["detail"]) for i in rep] == [(1, "Слово «в» повторено подряд"),
                                                       (1, "Слово «на» повторено подряд")]
    dates = issues(text, "date_format")
    assert len(dates) == 3 and all(i["line"] == 2 for i in dates)


def test_issues_sorted_and_stats():
    text = "1. А.\n3. В своевременно.\n"
    res = mod.check(text)
    lines = [i["line"] if i["line"] is not None else 10 ** 9 for i in res["issues"]]
    assert lines == sorted(lines)
    assert res["stats"]["points"] == 2
    assert res["stats"]["issues_by_type"]["numbering_gap"] == 1
    assert res["disclaimer"]


def test_weird_input_does_not_crash():
    for text in ("", " \n\t\n", "а)\n" * 100, "1.\n" * 100, "[" * 10000, " " * 1000, "пункт " * 5000,
                 "x" * 300000, "1.1.1.1.1.1.1.1. глубоко\n"):
        res = mod.check(text)
        assert "issues" in res


def test_large_input_is_fast():
    chunk = (SKILL / "examples" / "project.txt").read_text(encoding="utf-8")
    big = (chunk + "\n") * (1_100_000 // len(chunk))
    t0 = time.perf_counter()
    res = mod.check(big)
    assert time.perf_counter() - t0 < 10
    assert res["stats"]["lines"] > 10000


def test_example_matches_expected():
    text = (SKILL / "examples" / "project.txt").read_text(encoding="utf-8")
    expected = json.loads((SKILL / "examples" / "project.expected.json").read_text(encoding="utf-8"))
    assert mod.check(text) == expected


def test_cli(tmp_path):
    src = tmp_path / "a.txt"
    src.write_bytes(("1. Текст.\n3. Своевременно.\n").encode("cp1251"))
    code, out, _ = run_cli(str(src))
    assert code == 0
    data = json.loads(out)
    assert data["stats"]["issues_by_type"]["numbering_gap"] == 1
    code, out, _ = run_cli(str(src), "--format", "md")
    assert code == 0 and "## Замечания" in out and "### Ошибки" in out
    code, _, err = run_cli(str(tmp_path / "нет.txt"))
    assert code == 2 and "не найден" in err
    code, _, err = run_cli("-", stdin=b"")
    assert code == 2 and "пустой" in err
    code, out, _ = run_cli("-", stdin="1. Текст.\r\n2. Текст.\r\n".encode("utf-8"))
    assert code == 0 and json.loads(out)["stats"]["points"] == 2
