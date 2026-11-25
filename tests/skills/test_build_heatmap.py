import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "docs" / "usecases" / "skills" / "og-heatmap"
SCRIPT = SKILL / "scripts" / "build_heatmap.py"
EXAMPLE = SKILL / "examples" / "vladimir_by_territory.json"

_spec = importlib.util.spec_from_file_location("build_heatmap", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["build_heatmap"] = mod
_spec.loader.exec_module(mod)


def area(name, requests=1, overdue=0, questions=1, region="Владимирская область", source="municipal_area", topics=()):
    return {"area": name, "region": region, "source": source, "requests": requests, "questions": questions,
            "overdue": overdue, "top_topics": [{"name": t, "count": 1} for t in topics]}


def payload(*areas, region_filter="Владимирская"):
    return {"region_filter": region_filter, "requests_total": 100, "requests_in_region": sum(a["requests"] for a in areas),
            "requests_with_territory": sum(a["requests"] for a in areas), "requests_without_territory": 0,
            "areas": list(areas), "truncated": False}


def run_cli(*args, stdin=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


# --- нормализация и сопоставление названий ---------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("м.р-н. Муромский", ("муромск", "district")),
    ("Агаповский муниципальный округ", ("агаповск", "district")),
    ("г.о. город Ковров", ("ковров", "city")),
    ("г. Радужный", ("радужн", "city")),
    ("Озёрский городской округ", ("озерск", "city")),
])
def test_normalize(name, expected):
    assert mod.normalize(name) == expected


def test_vladimir_rx_names_match_one_to_one():
    region = mod.load_region("vladimir")
    data = payload(area("м.р-н. Муромский", 18), area("г.о. город Владимир", 2), area("г. Радужный", 1, source="city"))

    matched, unmatched = mod.match(region, data["areas"])

    assert unmatched == []
    assert matched["м.р-н. Муромский"]["requests"] == 18
    assert matched["г.о. ЗАТО город Радужный"]["requests"] == 1


def test_district_and_city_okrug_with_same_stem_are_not_mixed():
    region = mod.load_region("chelyabinsk")
    data = payload(area("м.р-н. Троицкий", 5, region="Челябинская область"),
                   area("г.о. город Троицк", 7, region="Челябинская область"),
                   area("г. Копейск", 3, region="Челябинская область", source="city"))

    matched, unmatched = mod.match(region, data["areas"])

    assert unmatched == []
    assert matched["Троицкий муниципальный округ"]["requests"] == 5
    assert matched["Троицкий городской округ"]["requests"] == 7
    assert matched["Копейский городской округ"]["requests"] == 3


def test_unknown_area_reported_not_dropped_silently():
    region = mod.load_region("vladimir")

    matched, unmatched = mod.match(region, [area("м.р-н. Несуществующий", 4)])

    assert matched == {} and unmatched == [{"area": "м.р-н. Несуществующий", "requests": 4}]


def test_same_area_twice_is_summed():
    region = mod.load_region("vladimir")

    matched, _ = mod.match(region, [area("г.о. ЗАТО город Радужный", 2, overdue=1), area("г. Радужный", 1, overdue=1, source="city")])

    assert matched["г.о. ЗАТО город Радужный"]["requests"] == 3 and matched["г.о. ЗАТО город Радужный"]["overdue"] == 2


@pytest.mark.parametrize("data, expected", [
    (payload(area("м.р-н. Муромский")), "vladimir"),
    (payload(area("Копейск", region="Челябинская область"), region_filter=None), "chelyabinsk"),
])
def test_region_detected_from_payload(data, expected):
    assert mod.detect_region(data) == expected


def test_region_not_detected_raises_input_error():
    with pytest.raises(mod.InputError, match="--region"):
        mod.detect_region(payload(area("г. Тюмень", region="Тюменская область"), region_filter=None))


# --- сборка ---------------------------------------------------------------------

def test_html_is_self_contained_with_paths_data_and_attribution():
    html = mod.build(payload(area("м.р-н. Муромский", 18, overdue=12, topics=["Семья"])), fmt="html")

    assert html.startswith("<!doctype html>")
    assert "Владимирская область" in html and "Муромский район" in html
    assert "OpenStreetMap" in html
    assert "<script src=" not in html and "https://" not in html.split("<script>")[1]  # без внешних библиотек и запросов
    assert '"requests":18' in html and "Семья" in html


def test_jsx_is_react_component_without_external_imports():
    jsx = mod.build(payload(area("м.р-н. Муромский", 18)), fmt="jsx")

    assert "export default function" in jsx
    assert 'from "react"' in jsx
    assert "recharts" not in jsx and "fetch(" not in jsx
    assert "Муромский район" in jsx


def test_unmatched_and_coverage_shown_in_output():
    data = payload(area("м.р-н. Муромский", 18), area("м.р-н. Несуществующий", 4))
    data["requests_without_territory"] = 9

    html = mod.build(data, fmt="html")

    assert "м.р-н. Несуществующий" in html and "без района на карте" in html


def test_metric_must_be_known():
    with pytest.raises(mod.InputError, match="metric"):
        mod.build(payload(area("м.р-н. Муромский")), metric="per_capita")


# --- командная строка -----------------------------------------------------------

def test_example_builds_html_file(tmp_path):
    out = tmp_path / "map.html"

    code, stdout, err = run_cli(str(EXAMPLE), "--out", str(out))

    assert code == 0, err
    assert Path(stdout.strip()) == out.resolve() and out.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_stdin_jsx_to_stdout():
    code, stdout, err = run_cli("-", "--format", "jsx", stdin=EXAMPLE.read_bytes())

    assert code == 0, err
    assert "export default function" in stdout


def test_bad_input_exit_code_2():
    code, _, err = run_cli("-", stdin=b'{"areas": "not a list"}')

    assert code == 2 and "Ошибка входных данных" in err
