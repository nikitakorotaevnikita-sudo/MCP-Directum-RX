"""GeoJSON с границами районов → геопакет региона для навыка og-heatmap (regions/<код>.json).

Вход: FeatureCollection, у каждого района — название в свойстве (district, name, NAME, name:ru, shapeName или --name-field).
Выход: {code, region, source, viewBox, areas: [{key, name, kind, aliases, path, cx, cy, pts}]} — SVG-пути
в проекции «равнопромежуточная с поправкой на широту», упрощённые до --max-points точек на весь регион.

Запуск:  python geojson_to_region.py РАЙОНЫ.geojson --code vladimir --region "Владимирская область"
                                    [--name-field district] [--max-points 4000] [--source "..."] [--out regions/vladimir.json]
Коды выхода: 0 — успех, 2 — ошибка входных данных, 1 — непредвиденная ошибка. Только стандартная библиотека.
"""

import argparse
import json
import math
import re
import sys
import traceback
from pathlib import Path

WIDTH = 1000.0
NAME_FIELDS = ("district", "name:ru", "name", "NAME", "shapeName", "NAME_RU")
DEFAULT_SOURCE = "© участники OpenStreetMap, ODbL"


class InputError(Exception):
    """Ошибка входных данных (код выхода 2)."""


def kind_of(name: str) -> str:
    low = name.lower()
    if "муниципальный округ" in low:
        return "муниципальный округ"
    if "район" in low or "муниципальн" in low or "м.р-н" in low or "улус" in low or "кожуун" in low:
        return "муниципальный район"
    return "городской округ"


def aliases_for(name: str, kind: str) -> list[str]:
    """Как район может называться в RX: «м.р-н. Муромский», «г. Ковров», «г.о. город Ковров»."""
    base = re.sub(r"(муниципальный|городской)\s+(район|округ)|(?<!\w)(район|округ|зато|город|г\.о\.|г\.)(?!\w)", " ",
                  name, flags=re.IGNORECASE)
    base = re.sub(r"\s+", " ", base).strip(" .,")
    if not base:
        return []
    if kind == "городской округ":
        return [f"г. {base}", f"г.о. город {base}", f"{base} городской округ"]
    return [f"м.р-н. {base}", f"{base} муниципальный район", f"{base} муниципальный округ"]


def outer_rings(geometry: dict) -> list:
    if not geometry:
        return []
    if geometry.get("type") == "Polygon":
        return [geometry["coordinates"][0]] if geometry.get("coordinates") else []
    if geometry.get("type") == "MultiPolygon":
        return [poly[0] for poly in geometry.get("coordinates") or [] if poly]
    return []


def simplify(points: list, tol: float) -> list:
    """Дуглас — Пекер. Замкнутый контур делим в самой дальней от начала точке."""
    if len(points) < 3:
        return points
    if points[0] == points[-1]:
        x0, y0 = points[0]
        far = max(range(len(points)), key=lambda i: (points[i][0] - x0) ** 2 + (points[i][1] - y0) ** 2)
        if 0 < far < len(points) - 1:
            return simplify(points[: far + 1], tol)[:-1] + simplify(points[far:], tol)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = points[a], points[b]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy) or 1e-12
        best, idx = 0.0, None
        for i in range(a + 1, b):
            px, py = points[i]
            d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > best:
                best, idx = d, i
        if idx is not None and best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(points, keep) if k]


def convert(collection: dict, code: str, region: str, name_field: str | None = None,
            max_points: int = 4000, source: str = DEFAULT_SOURCE) -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,60}", code or ""):
        raise InputError("--code: латиница в нижнем регистре, цифры и дефис (например, vladimir)")
    features = collection.get("features") if isinstance(collection, dict) else None
    if not isinstance(features, list) or not features:
        raise InputError("ожидался GeoJSON FeatureCollection с районами")
    items = []
    for i, feature in enumerate(features):
        props = feature.get("properties") or {}
        field = name_field or next((f for f in NAME_FIELDS if props.get(f)), None)
        name = str(props.get(field) or "").strip() if field else ""
        rings = [r for r in outer_rings(feature.get("geometry")) if len(r) >= 4]
        if not name:
            raise InputError(f"features[{i}]: нет названия (укажите --name-field)")
        if rings:
            items.append((name, rings))
    if not items:
        raise InputError("ни у одного района нет полигона")
    pts = [p for _, rings in items for r in rings for p in r]
    lat0 = math.radians((min(p[1] for p in pts) + max(p[1] for p in pts)) / 2)
    kx = math.cos(lat0)
    minx, maxx = min(p[0] for p in pts) * kx, max(p[0] for p in pts) * kx
    miny, maxy = -max(p[1] for p in pts), -min(p[1] for p in pts)
    scale = WIDTH / ((maxx - minx) or 1e-9)
    projected = [(name, [[((lon * kx - minx) * scale, (-lat - miny) * scale) for lon, lat, *_ in r] for r in rings])
                 for name, rings in items]
    tol = 0.3
    while True:  # подбираем упрощение под бюджет точек: карта лёгкая и одинаково подробная в любом регионе
        shapes = []
        for name, rings in projected:
            simple = []
            for ring in rings:
                s = simplify(ring, tol)
                if len(s) < 4:
                    s = simplify(ring, tol / 8)
                if len(s) >= 4:
                    simple.append(s)
            shapes.append((name, simple))
        total = sum(len(s) for _, rings in shapes for s in rings)
        if total <= max_points or tol > 50:
            break
        tol *= 1.5
    seen, areas = {}, []
    for name, rings in shapes:
        if not rings:
            continue
        key = name if name not in seen else f"{name} ({seen[name] + 1})"
        seen[name] = seen.get(name, 0) + 1
        flat = [p for r in rings for p in r]
        kind = kind_of(name)
        areas.append({
            "key": key, "name": name, "kind": kind, "aliases": aliases_for(name, kind),
            "path": "".join("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in r) + "Z" for r in rings),
            "cx": round(sum(x for x, _ in flat) / len(flat), 1), "cy": round(sum(y for _, y in flat) / len(flat), 1),
            "pts": len(flat),
        })
    return {"code": code, "region": region, "source": source,
            "viewBox": f"0 0 {WIDTH:.0f} {(maxy - miny) * scale:.0f}", "areas": sorted(areas, key=lambda a: a["name"])}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        _write(sys.stderr, f"Ошибка входных данных: {message}\n")
        sys.exit(2)


def _write(stream, text: str) -> None:
    stream.buffer.write(text.encode("utf-8"))
    stream.flush()


def main(argv=None) -> int:
    parser = _Parser(description="GeoJSON районов → геопакет региона для og-heatmap.")
    parser.add_argument("input", help="GeoJSON FeatureCollection с районами")
    parser.add_argument("--code", required=True, help="код пакета: латиница, например vladimir")
    parser.add_argument("--region", required=True, help="название региона, как в RX: «Владимирская область»")
    parser.add_argument("--name-field", help="свойство с названием района (по умолчанию — найти само)")
    parser.add_argument("--max-points", type=int, default=4000, help="бюджет точек на весь регион (по умолчанию 4000)")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="подпись об источнике границ")
    parser.add_argument("--out", help="файл пакета; по умолчанию regions/<код>.json рядом со скриптом")
    parser.add_argument("--debug", action="store_true", help="трассировка при ошибке")
    args = parser.parse_args(argv)
    try:
        try:
            collection = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InputError(f"не удалось прочитать GeoJSON: {exc}") from None
        pack = convert(collection, args.code, args.region, args.name_field, args.max_points, args.source)
        out = Path(args.out) if args.out else Path(__file__).resolve().parent.parent / "regions" / f"{args.code}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(pack, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        _write(sys.stdout, f"{out.resolve()}  районов: {len(pack['areas'])}, точек: {sum(a['pts'] for a in pack['areas'])}, "
                           f"{out.stat().st_size // 1024} КБ\n")
        return 0
    except InputError as exc:
        _write(sys.stderr, f"Ошибка входных данных: {exc}\n")
        return 2
    except Exception as exc:  # noqa: BLE001
        if args.debug:
            traceback.print_exc()
        _write(sys.stderr, f"Непредвиденная ошибка: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
