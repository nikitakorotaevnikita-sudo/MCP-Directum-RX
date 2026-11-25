"""Тепловая карта обращений граждан по районам региона — из ответа get_citizen_requests_by_territory.

Сопоставляет районы из RX с границами региона (папка regions/), красит районы по метрике и собирает
самодостаточный HTML-файл (SVG + немного JS, без внешних библиотек и запросов) или React-компонент
для артефакта.

Запуск:  python build_heatmap.py ВХОД.json|- [--region vladimir|chelyabinsk] [--metric requests|overdue|questions]
                                 [--format html|jsx] [--title "..."] [--out ФАЙЛ] [--debug]
Коды выхода: 0 — успех, 2 — ошибка входных данных, 1 — непредвиденная ошибка.
Только стандартная библиотека Python 3.10+.
"""

import argparse
import json
import re
import sys
import traceback
from pathlib import Path

REGIONS_DIR = Path(__file__).resolve().parent.parent / "regions"
MAX_BYTES = 20_000_000
METRICS = {"requests": "Обращения", "overdue": "Просрочено", "questions": "Вопросы"}

# Названия территорий в RX и в границах пишутся по-разному: «м.р-н. Муромский», «Агаповский муниципальный округ»,
# «г.о. город Ковров», «г. Радужный». Сравниваем по основе названия и виду: район или город (городской округ).
CITY_MARKERS = re.compile(r"городской\s+округ|(?<!\w)г\.\s*о\.|(?<!\w)г\.|(?<!\w)город(?!\w)|(?<!\w)зато(?!\w)|^округ\s")
DROP = [
    r"городской\s+округ(\s+с\s+внутригородским\s+делением)?", r"муниципальный\s+(район|округ)", r"(?<!\w)м\.?\s*р-н\.?",
    r"(?<!\w)м\.\s*о\.", r"(?<!\w)г\.\s*о\.", r"(?<!\w)зато(?!\w)", r"(?<!\w)город(?!\w)", r"(?<!\w)округ(?!\w)",
    r"(?<!\w)район(?!\w)", r"(?<!\w)р-н(?!\w)", r"(?<!\w)г\.",
]
ENDING = re.compile(r"(ский|цкий|ской|ий|ый|ой|ая)$")


class InputError(Exception):
    """Ошибка входных данных (код выхода 2)."""


def normalize(name: str) -> tuple[str, str]:
    """«м.р-н. Муромский» → ("муромск", "district"); «г.о. город Ковров» → ("ковров", "city")."""
    text = (name or "").lower().replace("ё", "е").strip()
    kind = "city" if CITY_MARKERS.search(text) else "district"
    for pattern in DROP:
        text = re.sub(pattern, " ", text)
    words = [w for w in re.split(r"\s+", text.strip(" .,:")) if w]
    stems = [re.sub(r"(ский|ской)$", "ск", ENDING.sub(lambda m: "ск" if m.group(1) in ("ский", "ской") else "ц" if m.group(1) == "цкий" else "", w))
             for w in words]
    return " ".join(stems), kind


def load_region(code: str) -> dict:
    path = REGIONS_DIR / f"{code}.json"
    if not path.exists():
        known = ", ".join(sorted(p.stem for p in REGIONS_DIR.glob("*.json")))
        raise InputError(f"нет геопакета региона «{code}». Доступны: {known}")
    return json.loads(path.read_text(encoding="utf-8"))


def detect_region(payload: dict) -> str:
    """Регион по region_filter или по полю region у районов: сравниваем с названиями геопакетов."""
    hints = [str(payload.get("region_filter") or "")] + [str(a.get("region") or "") for a in payload.get("areas") or []]
    text = " ".join(hints).lower().replace("ё", "е")
    for path in sorted(REGIONS_DIR.glob("*.json")):
        name = json.loads(path.read_text(encoding="utf-8"))["region"].lower().replace("ё", "е")
        if name.split()[0][:7] in text:
            return path.stem
    known = ", ".join(sorted(p.stem for p in REGIONS_DIR.glob("*.json")))
    raise InputError(f"не удалось определить регион по данным — укажите --region ({known})")


def match(region: dict, areas: list) -> tuple[dict, list]:
    """Район из RX → район карты. Сначала основа + вид (район / город), затем только основа, если она однозначна."""
    exact, by_stem = {}, {}
    for item in region["areas"]:
        for name in [item["key"], item["name"], *item.get("aliases", [])]:
            stem, kind = normalize(name)
            exact.setdefault((stem, kind), item["key"])
            by_stem.setdefault(stem, set()).add(item["key"])
    matched, unmatched = {}, []
    for row in areas:
        stem, kind = normalize(row.get("area", ""))
        key = exact.get((stem, kind))
        if key is None and len(by_stem.get(stem, ())) == 1:
            key = next(iter(by_stem[stem]))
        if key is None:
            unmatched.append({"area": row.get("area"), "requests": int(row.get("requests") or 0)})
            continue
        cur = matched.setdefault(key, {"requests": 0, "questions": 0, "overdue": 0, "top_topics": []})
        for metric in METRICS:
            cur[metric] += int(row.get(metric) or 0)
        topics = {t["name"]: t["count"] for t in cur["top_topics"]}
        for topic in row.get("top_topics") or []:
            topics[topic["name"]] = topics.get(topic["name"], 0) + int(topic.get("count") or 0)
        cur["top_topics"] = [{"name": n, "count": c} for n, c in sorted(topics.items(), key=lambda kv: -kv[1])[:3]]
    return matched, unmatched


def _validate(payload) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("areas"), list):
        raise InputError("ожидался ответ get_citizen_requests_by_territory: объект с полем areas (список районов)")
    for i, row in enumerate(payload["areas"]):
        if not isinstance(row, dict) or not row.get("area"):
            raise InputError(f"areas[{i}]: нужен объект с полем area")
    return payload


def _context(payload: dict, region_code: str | None, metric: str, title: str | None) -> dict:
    if metric not in METRICS:
        raise InputError(f"metric: одно из {', '.join(METRICS)}")
    payload = _validate(payload)
    region = load_region(region_code or detect_region(payload))
    matched, unmatched = match(region, payload["areas"])
    period = " – ".join(p for p in (payload.get("date_from"), payload.get("date_to")) if p) or "весь период"
    notes = [f"Период регистрации: {period}."]
    if payload.get("classifier_code"):
        notes.append(f"Код классификатора: {payload['classifier_code']}.")
    if payload.get("requests_without_territory"):
        notes.append(f"Без указанной территории: {payload['requests_without_territory']} обращ.")
    if unmatched:
        notes.append("Есть в RX, но без района на карте: " + "; ".join(f"{u['area']} ({u['requests']})" for u in unmatched) + ".")
    if payload.get("truncated"):
        notes.append("Учтены не все обращения (достигнут предел выборки) — сузьте период.")
    return {"region": region, "data": matched, "unmatched": unmatched, "metric": metric, "notes": " ".join(notes),
            "title": title or f"Обращения граждан по районам · {region['region']}"}


def build(payload: dict, region_code: str | None = None, metric: str = "requests", fmt: str = "html",
          title: str | None = None) -> str:
    ctx = _context(payload, region_code, metric, title)
    template = HTML_TEMPLATE if fmt == "html" else JSX_TEMPLATE
    geo = {k: ctx["region"][k] for k in ("region", "source", "viewBox")}
    geo["areas"] = [{k: a[k] for k in ("key", "name", "kind", "path", "cx", "cy", "pts")} for a in ctx["region"]["areas"]]
    replacements = {
        "__TITLE__": ctx["title"], "__NOTES__": ctx["notes"], "__SOURCE__": ctx["region"]["source"],
        "__VIEWBOX__": ctx["region"]["viewBox"], "__METRIC__": metric,
        "__GEO__": json.dumps(geo, ensure_ascii=False, separators=(",", ":")),
        "__DATA__": json.dumps(ctx["data"], ensure_ascii=False, separators=(",", ":")),
        "__METRICS__": json.dumps(METRICS, ensure_ascii=False),
    }
    out = template
    for marker, value in replacements.items():
        out = out.replace(marker, value if marker in ("__GEO__", "__DATA__", "__METRICS__") else _escape(value, fmt))
    return out


def _escape(text: str, fmt: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace("{", "&#123;").replace("}", "&#125;") if fmt == "jsx" else text


HTML_TEMPLATE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--navy:#13406D;--create:#1A73E8;--red:#D93025;--page:#F5F7FA;--border:#DDE3ED;--text:#1C2B3A;--muted:#6B7A90;--hint:#8A98B0}
*{box-sizing:border-box}body{margin:0;background:var(--page);font:13px "Segoe UI",system-ui,sans-serif;color:var(--text)}
.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:16px;padding:20px}
.card{background:#fff;border:1px solid var(--border);border-radius:8px}
.head{display:flex;align-items:center;gap:10px;padding:13px 16px;border-bottom:1px solid #eef1f5;font-size:15.5px;font-weight:600}
.body{padding:12px 14px}
.kpi{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.kpi div{background:var(--page);border-radius:7px;padding:11px 12px}
.kpi b{display:block;font-size:26px;color:var(--create);font-variant-numeric:tabular-nums}.kpi .red b{color:var(--red)}.kpi span{font-size:12px;font-weight:600}
.tabs{display:flex;gap:6px;margin-left:auto}.tabs button{border:1px solid var(--border);background:#fff;border-radius:14px;padding:3px 11px;font:inherit;font-size:12px;cursor:pointer;color:var(--muted)}
.tabs button.on{background:var(--create);border-color:var(--create);color:#fff}
svg path{stroke:#fff;stroke-width:1;cursor:pointer}svg path:hover{stroke:var(--navy);stroke-width:2.2}
svg text{font-size:12px;fill:var(--text);pointer-events:none;paint-order:stroke;stroke:#fff;stroke-width:3px}
.legend{display:flex;gap:4px;align-items:center;margin-top:8px;color:var(--muted);font-size:11px}.legend i{display:inline-block;width:30px;height:10px;border-radius:2px}
.tip{position:fixed;pointer-events:none;background:#fff;border:1px solid var(--border);border-radius:6px;padding:8px 10px;font-size:12px;display:none;box-shadow:0 2px 10px rgba(20,40,70,.1);max-width:300px}
table{width:100%;border-collapse:collapse;font-size:12px}th{font-size:10.5px;text-transform:uppercase;color:var(--hint);text-align:right;padding-bottom:6px;border-bottom:1px solid var(--border)}
th:first-child,td:first-child{text-align:left}td{padding:7px 0;border-bottom:1px solid #f3f5f8;text-align:right;font-variant-numeric:tabular-nums}td.r{color:var(--red);font-weight:700}
.note{color:var(--muted);font-size:11px;line-height:1.45;margin-top:8px}
@media(max-width:900px){.grid>.card{grid-column:span 12!important}.kpi{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style></head><body><div class="grid">
<section class="card" style="grid-column:span 12"><div class="head">__TITLE__</div><div class="body"><div class="kpi" id="kpi"></div><div class="note">__NOTES__</div></div></section>
<section class="card" style="grid-column:span 8"><div class="head">Карта<div class="tabs" id="tabs"></div></div>
<div class="body"><svg id="map" viewBox="__VIEWBOX__" width="100%"></svg><div class="legend" id="legend"></div><div class="note">Границы: __SOURCE__.</div></div></section>
<section class="card" style="grid-column:span 4"><div class="head">Районы</div><div class="body"><table id="tbl"></table></div></section>
</div><div class="tip" id="tip"></div>
<script>
const GEO=__GEO__, DATA=__DATA__, METRICS=__METRICS__;
const PAL={requests:["#E8F0FB","#B9D3F3","#7FAEE8","#3F86E0","#13406D"],questions:["#E8F0FB","#B9D3F3","#7FAEE8","#3F86E0","#13406D"],overdue:["#FDECEB","#F7C4BF","#EE8F86","#E3574B","#D93025"]};
let metric="__METRIC__";const svg=document.getElementById("map"),tip=document.getElementById("tip");
const val=k=>(DATA[k]||{})[metric]||0, esc=s=>String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
function draw(){svg.innerHTML="";const max=Math.max(1,...GEO.areas.map(a=>val(a.key)));
 const color=v=>v<=0?"#EEF1F5":PAL[metric][Math.min(4,Math.floor(v/max*4.999))];
 GEO.areas.slice().sort((a,b)=>b.pts-a.pts).forEach(a=>{const p=document.createElementNS("http://www.w3.org/2000/svg","path"),d=DATA[a.key];
  p.setAttribute("d",a.path);p.setAttribute("fill",color(val(a.key)));
  p.onmousemove=e=>{tip.style.display="block";tip.style.left=e.clientX+14+"px";tip.style.top=e.clientY+10+"px";
   tip.innerHTML="<b>"+esc(a.name)+"</b><br>"+(d?"Обращений: "+d.requests+" · вопросов: "+d.questions+"<br>Просрочено: "+d.overdue+
   (d.top_topics.length?"<br><span style='color:#6B7A90'>Чаще всего: "+d.top_topics.map(t=>esc(t.name)+" ("+t.count+")").join("; ")+"</span>":""):"Обращений нет");};
  p.onmouseleave=()=>tip.style.display="none";svg.appendChild(p);});
 GEO.areas.filter(a=>val(a.key)>0).forEach(a=>{const t=document.createElementNS("http://www.w3.org/2000/svg","text");
  t.setAttribute("x",a.cx);t.setAttribute("y",a.cy);t.setAttribute("text-anchor","middle");t.textContent=val(a.key);svg.appendChild(t);});
 document.getElementById("legend").innerHTML="0 <i style='background:#EEF1F5'></i>"+PAL[metric].map(c=>"<i style='background:"+c+"'></i>").join("")+" "+max;
 document.getElementById("tabs").innerHTML=Object.entries(METRICS).map(([k,l])=>"<button data-m='"+k+"' class='"+(k===metric?"on":"")+"'>"+l+"</button>").join("");
 document.querySelectorAll("#tabs button").forEach(b=>b.onclick=()=>{metric=b.dataset.m;draw();});}
const tot=k=>Object.values(DATA).reduce((s,d)=>s+d[k],0), withData=GEO.areas.filter(a=>DATA[a.key]).length;
document.getElementById("kpi").innerHTML="<div><b>"+tot("requests")+"</b><span>Обращений на карте</span></div><div><b>"+tot("questions")+"</b><span>Вопросов</span></div>"+
 "<div class='red'><b>"+tot("overdue")+"</b><span>Просрочено</span></div><div><b>"+withData+" из "+GEO.areas.length+"</b><span>Районов с обращениями</span></div>";
document.getElementById("tbl").innerHTML="<tr><th>Район</th><th>Обращ.</th><th>Просроч.</th></tr>"+GEO.areas.filter(a=>DATA[a.key])
 .sort((a,b)=>DATA[b.key].requests-DATA[a.key].requests).map(a=>"<tr><td>"+esc(a.name)+"</td><td>"+DATA[a.key].requests+"</td><td class='"+(DATA[a.key].overdue?"r":"")+"'>"+DATA[a.key].overdue+"</td></tr>").join("");
draw();
</script></body></html>
"""

JSX_TEMPLATE = """import { useState } from "react";

// Тепловая карта обращений граждан по районам. Данные: get_citizen_requests_by_territory (mcpOGV), только агрегаты.
// Границы: __SOURCE__.
const GEO = __GEO__;
const DATA = __DATA__;
const METRICS = __METRICS__;
const T = { navy: "#13406D", create: "#1A73E8", red: "#D93025", page: "#F5F7FA", border: "#DDE3ED", text: "#1C2B3A", muted: "#6B7A90", hint: "#8A98B0" };
const PAL = {
  requests: ["#E8F0FB", "#B9D3F3", "#7FAEE8", "#3F86E0", "#13406D"],
  questions: ["#E8F0FB", "#B9D3F3", "#7FAEE8", "#3F86E0", "#13406D"],
  overdue: ["#FDECEB", "#F7C4BF", "#EE8F86", "#E3574B", "#D93025"],
};
const card = { background: "#fff", border: `1px solid ${T.border}`, borderRadius: 8 };
const head = { display: "flex", alignItems: "center", gap: 10, padding: "13px 16px", borderBottom: "1px solid #eef1f5", fontSize: 15.5, fontWeight: 600 };
const total = (k) => Object.values(DATA).reduce((s, d) => s + d[k], 0);

export default function OgHeatmap() {
  const [metric, setMetric] = useState("__METRIC__");
  const [hover, setHover] = useState(null);
  const val = (k) => (DATA[k] || {})[metric] || 0;
  const max = Math.max(1, ...GEO.areas.map((a) => val(a.key)));
  const color = (v) => (v <= 0 ? "#EEF1F5" : PAL[metric][Math.min(4, Math.floor((v / max) * 4.999))]);
  const rows = GEO.areas.filter((a) => DATA[a.key]).sort((a, b) => DATA[b.key].requests - DATA[a.key].requests);
  const hovered = hover && GEO.areas.find((a) => a.key === hover);
  const hd = hover && DATA[hover];
  return (
    <div style={{ background: T.page, padding: 20, fontFamily: '"Segoe UI",system-ui,sans-serif', fontSize: 13, color: T.text }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(12,minmax(0,1fr))", gap: 16 }}>
        <section style={{ ...card, gridColumn: "span 12" }}>
          <div style={head}>__TITLE__</div>
          <div style={{ padding: "12px 14px" }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4,minmax(0,1fr))", gap: 10 }}>
              {[["requests", "Обращений на карте"], ["questions", "Вопросов"], ["overdue", "Просрочено"]].map(([k, l]) => (
                <div key={k} style={{ background: T.page, borderRadius: 7, padding: "11px 12px" }}>
                  <div style={{ fontSize: 26, fontWeight: 700, color: k === "overdue" ? T.red : T.create }}>{total(k)}</div>
                  <div style={{ fontSize: 12, fontWeight: 600 }}>{l}</div>
                </div>
              ))}
              <div style={{ background: T.page, borderRadius: 7, padding: "11px 12px" }}>
                <div style={{ fontSize: 26, fontWeight: 700, color: T.create }}>{rows.length} из {GEO.areas.length}</div>
                <div style={{ fontSize: 12, fontWeight: 600 }}>Районов с обращениями</div>
              </div>
            </div>
            <div style={{ color: T.muted, fontSize: 11, marginTop: 8 }}>__NOTES__</div>
          </div>
        </section>
        <section style={{ ...card, gridColumn: "span 8" }}>
          <div style={head}>
            Карта
            <div style={{ display: "flex", gap: 6, marginLeft: "auto" }}>
              {Object.entries(METRICS).map(([k, l]) => (
                <button key={k} onClick={() => setMetric(k)} style={{ border: `1px solid ${k === metric ? T.create : T.border}`, background: k === metric ? T.create : "#fff", color: k === metric ? "#fff" : T.muted, borderRadius: 14, padding: "3px 11px", fontSize: 12, cursor: "pointer" }}>{l}</button>
              ))}
            </div>
          </div>
          <div style={{ padding: "12px 14px" }}>
            <svg viewBox="__VIEWBOX__" width="100%">
              {GEO.areas.slice().sort((a, b) => b.pts - a.pts).map((a) => (
                <path key={a.key} d={a.path} fill={color(val(a.key))} stroke={hover === a.key ? T.navy : "#fff"} strokeWidth={hover === a.key ? 2.2 : 1}
                  onMouseEnter={() => setHover(a.key)} onMouseLeave={() => setHover(null)} style={{ cursor: "pointer" }} />
              ))}
              {GEO.areas.filter((a) => val(a.key) > 0).map((a) => (
                <text key={"t" + a.key} x={a.cx} y={a.cy} textAnchor="middle" fontSize="12" fill={T.text} stroke="#fff" strokeWidth="3" paintOrder="stroke" style={{ pointerEvents: "none" }}>{val(a.key)}</text>
              ))}
            </svg>
            <div style={{ color: T.muted, fontSize: 12, minHeight: 34, marginTop: 6 }}>
              {hovered ? (
                <span><b style={{ color: T.text }}>{hovered.name}</b>{hd ? ` — обращений ${hd.requests}, вопросов ${hd.questions}, просрочено ${hd.overdue}` + (hd.top_topics.length ? `; чаще всего: ${hd.top_topics.map((t) => t.name + " (" + t.count + ")").join("; ")}` : "") : " — обращений нет"}</span>
              ) : "Наведите на район, чтобы увидеть цифры"}
            </div>
            <div style={{ color: T.muted, fontSize: 11 }}>Границы: __SOURCE__.</div>
          </div>
        </section>
        <section style={{ ...card, gridColumn: "span 4" }}>
          <div style={head}>Районы</div>
          <div style={{ padding: "12px 14px" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead><tr><th style={{ textAlign: "left", color: T.hint }}>Район</th><th style={{ textAlign: "right", color: T.hint }}>Обращ.</th><th style={{ textAlign: "right", color: T.hint }}>Просроч.</th></tr></thead>
              <tbody>
                {rows.map((a) => (
                  <tr key={a.key} onMouseEnter={() => setHover(a.key)} onMouseLeave={() => setHover(null)}>
                    <td style={{ padding: "7px 0", borderBottom: "1px solid #f3f5f8" }}>{a.name}</td>
                    <td style={{ textAlign: "right", borderBottom: "1px solid #f3f5f8" }}>{DATA[a.key].requests}</td>
                    <td style={{ textAlign: "right", borderBottom: "1px solid #f3f5f8", color: DATA[a.key].overdue ? T.red : T.text, fontWeight: DATA[a.key].overdue ? 700 : 400 }}>{DATA[a.key].overdue}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}
"""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        _write(sys.stderr, f"Ошибка входных данных: {message}\n")
        sys.exit(2)


def _write(stream, text: str) -> None:
    stream.buffer.write(text.encode("utf-8"))
    stream.flush()


def _read(source: str):
    try:
        raw = sys.stdin.buffer.read() if source == "-" else Path(source).read_bytes()
    except OSError as exc:
        raise InputError(f"не удалось прочитать {source}: {exc}") from None
    if len(raw) > MAX_BYTES:
        raise InputError("вход больше 20 МБ")
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError(f"вход — не JSON: {exc}") from None


def main(argv=None) -> int:
    parser = _Parser(description="Тепловая карта обращений граждан по районам региона.")
    parser.add_argument("input", help="ответ get_citizen_requests_by_territory (JSON) или «-»")
    parser.add_argument("--region", help="код геопакета: vladimir, chelyabinsk (по умолчанию — по данным)")
    parser.add_argument("--metric", default="requests", help="requests | overdue | questions")
    parser.add_argument("--format", choices=["html", "jsx"], default="html", help="html — файл, jsx — React-артефакт")
    parser.add_argument("--title", help="заголовок карты")
    parser.add_argument("--out", help="файл результата; без него — в stdout")
    parser.add_argument("--debug", action="store_true", help="трассировка при ошибке")
    args = parser.parse_args(argv)
    try:
        result = build(_read(args.input), region_code=args.region, metric=args.metric, fmt=args.format, title=args.title)
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(result, encoding="utf-8")
            _write(sys.stdout, str(out.resolve()) + "\n")
        else:
            _write(sys.stdout, result)
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
