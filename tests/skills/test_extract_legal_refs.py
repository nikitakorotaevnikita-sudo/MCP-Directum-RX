import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "docs" / "usecases" / "skills" / "pa-legal-basis"
SCRIPT = SKILL / "scripts" / "extract_legal_refs.py"

_spec = importlib.util.spec_from_file_location("extract_legal_refs", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["extract_legal_refs"] = mod
_spec.loader.exec_module(mod)


def run_cli(*args, stdin=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def refs(text):
    return mod.extract(text)["references"]


def one(text):
    found = refs(text)
    assert len(found) == 1, found
    return found[0]


def test_federal_law_full():
    r = one("В соответствии с Федеральным законом от 02.05.2006 № 59-ФЗ "
            "«О порядке рассмотрения обращений граждан Российской Федерации» постановляю:")
    assert r["kind"] == "federal_law"
    assert r["kind_label"] == "Федеральный закон"
    assert r["date"] == "2006-05-02"
    assert r["number"] == "59-ФЗ"
    assert r["title"] == "О порядке рассмотрения обращений граждан Российской Федерации"
    assert r["search_query"] == "Федеральный закон от 02.05.2006 № 59-ФЗ"
    assert r["occurrences"][0]["line"] == 1


def test_number_variants_and_word_dates():
    for text in ("Федеральный закон от 2 мая 2006 года N 59-ФЗ", "Федеральный закон от 02 мая 2006 г. No 59-ФЗ",
                 "Федеральный закон от 2.5.2006 №59-фз", "Федеральный закон № 59-ФЗ от 02.05.2006"):
        r = one(text)
        assert (r["date"], r["number"]) == ("2006-05-02", "59-ФЗ"), text


def test_nbsp_and_dashes():
    r = one("Федеральный закон от 02.05.2006 № 59–ФЗ")
    assert r["number"] == "59-ФЗ"
    assert r["date"] == "2006-05-02"


def test_dedupe_and_merge_undated_mentions():
    text = ("1. Федеральный закон от 02.05.2006 № 59-ФЗ «О порядке рассмотрения обращений граждан Российской Федерации».\n"
            "2. Согласно Федеральному закону № 59-ФЗ.\n"
            "3. Как указано в Федеральном законе от 02.05.2006 N 59-ФЗ.\n"
            "4. Федеральный закон «О порядке рассмотрения обращений граждан Российской Федерации».\n")
    r = one(text)
    assert [o["line"] for o in r["occurrences"]] == [1, 2, 3, 4]


def test_named_only_reference():
    r = one("Обработка ведётся согласно Федеральному закону «О персональных данных».")
    assert r["number"] is None and r["date"] is None
    assert r["title"] == "О персональных данных"
    assert r["search_query"] == "Федеральный закон «О персональных данных»"


def test_generic_phrase_is_not_reference():
    assert refs("в случаях, установленных федеральным законом, и законодательством Пермского края") == []


def test_codes_and_abbreviations():
    text = ("статьей 78 Бюджетного кодекса Российской Федерации, статьей 72 ГК РФ, КоАП РФ, "
            "Кодексом Российской Федерации об административных правонарушениях, Градостроительным кодексом РФ, "
            "статьей 12 БК РФ")
    found = refs(text)
    names = [r["name"] for r in found]
    assert names == ["Бюджетный кодекс Российской Федерации", "Гражданский кодекс Российской Федерации",
                     "Кодекс Российской Федерации об административных правонарушениях",
                     "Градостроительный кодекс Российской Федерации"]
    assert all(r["kind"] == "code" for r in found)
    assert len(found[0]["occurrences"]) == 2
    assert len(found[2]["occurrences"]) == 2


def test_constitution_decree_government_ministry():
    text = ("Конституцией Российской Федерации, Указом Президента РФ от 07.05.2012 № 601, "
            "постановлением Правительства Российской Федерации от 26.02.2010 № 96, "
            "распоряжением Правительства РФ от 17.12.2009 № 1993-р, приказом Минфина России от 06.12.2010 № 162н, "
            "Федеральным конституционным законом от 17.12.1997 № 2-ФКЗ")
    kinds = [(r["kind"], r["number"]) for r in refs(text)]
    assert kinds == [("constitution", None), ("presidential_decree", "601"), ("government_resolution", "96"),
                     ("government_order", "1993-р"), ("ministry_order", "162н"),
                     ("federal_constitutional_law", "2-ФКЗ")]
    ministry = refs(text)[4]
    assert ministry["issuer"] == "Минфина России"
    assert ministry["search_query"] == "Приказ Минфина России от 06.12.2010 № 162н"


def test_regional_law_and_acts():
    text = ("Законом Пермского края от 07.09.2007 № 108-ПК, постановлением Правительства Пермского края "
            "от 01.02.2021 № 45-п, распоряжением администрации города Перми от 10.03.2022 № 123-рп, "
            "решением Пермской городской Думы от 28.08.2007 № 185, письмом от 01.02.2023 № 12")
    found = refs(text)
    assert [(r["kind"], r["number"]) for r in found] == [
        ("regional_law", "108-ПК"), ("regional_act", "45-п"), ("regional_act", "123-рп"),
        ("regional_act", "185"), ("other_act", "12")]
    assert found[0]["search_query"] == "Закон Пермского края от 07.09.2007 № 108-ПК"
    assert found[1]["issuer"] == "Правительства Пермского края"
    assert found[2]["search_query"] == "Распоряжение администрации города Перми от 10.03.2022 № 123-рп"


def test_redaction_date():
    r = one("Федеральный закон от 27.07.2010 № 210-ФЗ «Об организации предоставления государственных и "
            "муниципальных услуг» (в ред. от 04.08.2023)")
    assert r["redaction_date"] == "2023-08-04"
    r = one("Федеральный закон от 27.07.2010 № 210-ФЗ (в редакции от 4 августа 2023 г.)")
    assert r["redaction_date"] == "2023-08-04"


def test_word_boundaries_no_false_positive():
    assert refs("указанного в законодательстве порядка, постановлениями от руки") == []


def test_sorted_by_first_occurrence():
    text = "Указ Президента РФ от 07.05.2012 № 601\nФедеральный закон от 02.05.2006 № 59-ФЗ\nУказ Президента РФ № 601"
    found = refs(text)
    assert [r["kind"] for r in found] == ["presidential_decree", "federal_law"]
    assert [o["line"] for o in found[0]["occurrences"]] == [1, 3]


def test_weird_input_does_not_crash():
    for text in ("", "   \n\t\n", "«" * 5000, "Федеральный закон от 99.99.9999 № ", "№" * 1000,
                 "закон области " * 1000, "x" * 100000):
        result = mod.extract(text)
        assert result["total"] == len(result["references"])
    assert one("Федеральный закон от 99.99.2006 № 59-ФЗ")["date"] is None


def test_large_input_is_fast():
    chunk = (SKILL / "examples" / "project.txt").read_text(encoding="utf-8")
    big = (chunk + "\n") * (1_100_000 // len(chunk))
    assert len(big) >= 1_000_000
    t0 = time.perf_counter()
    result = mod.extract(big)
    assert time.perf_counter() - t0 < 10
    assert result["total"] == mod.extract(chunk)["total"]


def test_example_matches_expected():
    text = (SKILL / "examples" / "project.txt").read_text(encoding="utf-8")
    expected = json.loads((SKILL / "examples" / "project.expected.json").read_text(encoding="utf-8"))
    assert mod.extract(text) == expected


def test_cli_json_md_and_codes(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("Федеральный закон от 02.05.2006 № 59-ФЗ", encoding="utf-8")
    code, out, _ = run_cli(str(src))
    assert code == 0
    data = json.loads(out)
    assert data["total"] == 1 and data["disclaimer"]
    code, out, _ = run_cli(str(src), "--format", "md")
    assert code == 0 and "# Ссылки на правовые акты" in out and "Запрос для поиска" in out
    code, _, err = run_cli(str(tmp_path / "нет.txt"))
    assert code == 2 and "не найден" in err
    code, _, err = run_cli("-", stdin=b"   \n")
    assert code == 2 and "пустой" in err
    code, _, err = run_cli(str(src), "--format", "xml")
    assert code == 2 and "Ошибка аргументов" in err


def test_cli_stdin_cp1251_and_bom(tmp_path):
    payload = "Федеральный закон от 02.05.2006 № 59-ФЗ".encode("cp1251")
    code, out, _ = run_cli("-", stdin=payload)
    assert code == 0 and json.loads(out)["references"][0]["number"] == "59-ФЗ"
    code, out, _ = run_cli("-", stdin=b"\xef\xbb\xbf" + "Закон Пермского края от 07.09.2007 № 108-ПК".encode("utf-8"))
    assert code == 0 and json.loads(out)["references"][0]["kind"] == "regional_law"


def test_cli_too_large(tmp_path):
    src = tmp_path / "big.txt"
    src.write_text("а" * 2_000_001, encoding="utf-8")
    code, _, err = run_cli(str(src))
    assert code == 2 and "слишком большой" in err


def test_law_of_rf_and_short_law_reference():
    found = refs("Закон РФ от 07.02.1992 № 2300-1 «О защите прав потребителей», статья 5 Закона № 59-ФЗ")
    assert [(r["kind"], r["search_query"]) for r in found] == [
        ("federal_law", "Закон Российской Федерации от 07.02.1992 № 2300-1"),
        ("federal_law", "Федеральный закон № 59-ФЗ")]
