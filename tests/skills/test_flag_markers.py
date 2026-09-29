import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "docs" / "usecases" / "skills" / "pa-anticorruption"
SCRIPT = SKILL / "scripts" / "flag_markers.py"

_spec = importlib.util.spec_from_file_location("flag_markers", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["flag_markers"] = mod
_spec.loader.exec_module(mod)


def run_cli(*args, stdin=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def by_code(text):
    return {f["code"]: f for f in mod.flag(text)["factors"]}


def markers(text, code):
    f = by_code(text).get(code)
    return [m["marker"] for m in f["markers_found"]] if f else []


def test_disclaimer_is_exact():
    assert mod.flag("текст")["disclaimer"] == ("Маркеры — повод проверить формулировку, а не вывод о "
                                              "коррупциогенности. Вывод делает эксперт по Методике № 96.")


def test_vprave_counts_for_two_factors():
    res = by_code("Орган вправе отказать.")
    assert set(res) == {"3а", "3б"}
    assert res["3а"]["markers_found"][0] == {
        "marker": "вправе", "line": 1, "found": "вправе", "context": "Орган вправе отказать."}


def test_word_boundaries():
    assert mod.flag("Неправомерно; правее; невправедливо; иномарка; мочь; можетбыть")["factors"] == []
    assert markers("Он может.", "3б") == ["может"]
    assert markers("ИНЫЕ документы", "4а") == ["иные документы"]


def test_gap_markers():
    assert markers("Порядок устанавливается уполномоченным исполнительным органом края.", "3г") == [
        "устанавливается … органом"]
    assert markers("Порядок определяется органом.", "3г") == ["определяется … органом"]
    assert markers("Порядок устанавливается в целом очень долго и сложно не органом.", "3г") == []
    assert markers("по решению министерства без проведения торгов", "3з") == [
        "по решению … без", "без проведения торгов"]


def test_all_factors_order_and_codes():
    text = ("По усмотрению органа; имеет право; в отдельных случаях; в порядке, определяемом министерством; "
            "в порядке, установленном законодательством; единственного поставщика; дополнительные документы; "
            "и т. п.")
    codes = [f["code"] for f in mod.flag(text)["factors"]]
    assert codes == ["3а", "3б", "3в", "3г", "3ж", "3з", "4а", "4в"]


def test_nbsp_dashes_and_case():
    text = "В ИСКЛЮЧИТЕЛЬНЫХ случаях — по согласованию с комиссией"
    assert markers(text, "3а") == ["в исключительных случаях"]
    assert markers(text, "3в") == ["по согласованию с"]


def test_no_duplicate_same_position_within_factor():
    res = by_code("а также иные документы")
    found = [(m["marker"], m["found"]) for m in res["4а"]["markers_found"]]
    assert found == [("а также иные", "а также иные"), ("иные документы", "иные документы")]
    # «в порядке, установленном» в 3г и полный маркер в 3ж — в разных факторах допустимо.
    res = by_code("в порядке, установленном законодательством")
    assert res["3г"]["count"] == 1 and res["3ж"]["count"] == 1


def test_lines_and_context_single_line():
    text = "Первая строка.\n" + "слово " * 40 + "\nОрган\nвправе\nотказать " + "слово " * 40
    m = by_code(text)["3б"]["markers_found"][0]
    assert m["line"] == 4
    assert "\n" not in m["context"]
    assert m["context"].startswith("…") and m["context"].endswith("…")
    assert len(m["context"]) <= 2 * 80 + len("вправе") + 2


def test_total_matches():
    res = mod.flag("вправе\nвправе")
    assert res["total_matches"] == 4
    assert sum(f["count"] for f in res["factors"]) == res["total_matches"]


def test_weird_input():
    for text in ("", "\n\n", "…" * 1000, "вправе" * 1000, "по решению " * 5000, "устанавливается " * 5000):
        mod.flag(text)


def test_large_input_is_fast():
    chunk = (SKILL / "examples" / "project.txt").read_text(encoding="utf-8")
    big = (chunk + "\n") * (1_100_000 // len(chunk))
    t0 = time.perf_counter()
    res = mod.flag(big)
    assert time.perf_counter() - t0 < 10
    assert res["total_matches"] == mod.flag(chunk)["total_matches"] * (1_100_000 // len(chunk))


def test_example_matches_expected():
    text = (SKILL / "examples" / "project.txt").read_text(encoding="utf-8")
    expected = json.loads((SKILL / "examples" / "project.expected.json").read_text(encoding="utf-8"))
    assert mod.flag(text) == expected


def test_cli(tmp_path):
    src = tmp_path / "a.txt"
    src.write_bytes("Орган вправе по своему усмотрению отказать.".encode("cp1251"))
    code, out, _ = run_cli(str(src))
    assert code == 0
    assert [f["code"] for f in json.loads(out)["factors"]] == ["3а", "3б"]
    code, out, _ = run_cli(str(src), "--format", "md")
    assert code == 0 and "# Маркеры коррупциогенных факторов" in out and "## Фактор 3а" in out
    code, _, err = run_cli(str(tmp_path / "нет.txt"))
    assert code == 2 and "не найден" in err
    code, _, err = run_cli("-", stdin=b" \n ")
    assert code == 2 and "пустой" in err
    code, _, err = run_cli()
    assert code == 2 and "Ошибка аргументов" in err
