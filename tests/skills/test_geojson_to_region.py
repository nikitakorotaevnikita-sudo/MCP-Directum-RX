import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "docs" / "usecases" / "skills" / "og-heatmap" / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


conv = load("geojson_to_region")
heat = load("build_heatmap")


def square(x, y, d=0.5):
    return [[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]


COLLECTION = {"type": "FeatureCollection", "features": [
    {"properties": {"district": "Муромский район"}, "geometry": {"type": "Polygon", "coordinates": [square(41.0, 55.5)]}},
    {"properties": {"district": "округ Муром"}, "geometry": {"type": "Polygon", "coordinates": [square(42.0, 55.5, 0.1)]}},
    {"properties": {"district": "ЗАТО Радужный"}, "geometry": {"type": "MultiPolygon",
                                                               "coordinates": [[square(40.3, 55.9, 0.05)], [square(40.4, 55.9, 0.05)]]}},
]}


def test_convert_builds_pack_in_skill_format():
    pack = conv.convert(COLLECTION, "test-region", "Тестовая область")

    assert pack["code"] == "test-region" and pack["region"] == "Тестовая область"
    assert pack["viewBox"].startswith("0 0 1000 ")
    names = {a["name"]: a for a in pack["areas"]}
    assert names["Муромский район"]["kind"] == "муниципальный район"
    assert names["округ Муром"]["kind"] == "городской округ"
    assert names["ЗАТО Радужный"]["path"].count("M") == 2  # мультиполигон — два контура
    for area in pack["areas"]:
        assert {"key", "name", "kind", "aliases", "path", "cx", "cy", "pts"} <= set(area)


def test_aliases_let_rx_names_match_converted_pack(tmp_path, monkeypatch):
    pack = conv.convert(COLLECTION, "test-region", "Тестовая область")
    (tmp_path / "test-region.json").write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(heat, "REGIONS_DIR", tmp_path)

    matched, unmatched = heat.match(heat.load_region("test-region"), [
        {"area": "м.р-н. Муромский", "requests": 5}, {"area": "г.о. округ Муром", "requests": 2},
        {"area": "г. Радужный", "requests": 1}])

    assert unmatched == []
    assert matched["Муромский район"]["requests"] == 5
    assert matched["округ Муром"]["requests"] == 2
    assert matched["ЗАТО Радужный"]["requests"] == 1


def test_point_budget_respected():
    import math
    ring = [[41 + 0.5 * math.cos(t / 50), 55 + 0.3 * math.sin(t / 50)] for t in range(0, 315)] + [[41.5, 55.0]]
    ring.append(ring[0])
    big = {"type": "FeatureCollection", "features": [{"properties": {"name": "Большой район"},
                                                     "geometry": {"type": "Polygon", "coordinates": [ring]}}]}

    pack = conv.convert(big, "big", "Регион", max_points=40)

    assert sum(a["pts"] for a in pack["areas"]) <= 40


@pytest.mark.parametrize("collection, message", [
    ({"type": "FeatureCollection", "features": []}, "FeatureCollection"),
    ({"features": [{"properties": {}, "geometry": {"type": "Polygon", "coordinates": [square(1, 1)]}}]}, "нет названия"),
])
def test_bad_input(collection, message):
    with pytest.raises(conv.InputError, match=message):
        conv.convert(collection, "x", "Регион")


def test_bad_code():
    with pytest.raises(conv.InputError, match="--code"):
        conv.convert(COLLECTION, "Владимир", "Регион")


def test_cli_writes_pack(tmp_path):
    src = tmp_path / "in.geojson"
    src.write_text(json.dumps(COLLECTION, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "regions" / "t.json"

    proc = subprocess.run([sys.executable, str(SCRIPTS / "geojson_to_region.py"), str(src), "--code", "t",
                           "--region", "Тестовая область", "--out", str(out)], capture_output=True, timeout=120)

    assert proc.returncode == 0, proc.stderr.decode("utf-8")
    assert json.loads(out.read_text(encoding="utf-8"))["areas"]
