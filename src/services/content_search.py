"""Поиск документов по примерному содержанию текста — одним вызовом, с ограниченным объёмом работы.

У сервиса интеграции RX нет запроса полнотекстового поиска, а RAG (QASearch) ищет только по документам, включённым
в области. Поэтому кандидаты собираются из трёх источников, тексты кандидатов читаются и сравниваются с образцом:
1. проиндексированные RAG-области документов (если есть и нашлось по существу);
2. документы, в названии или теме которых есть редкие слова образца или фамилия из него;
3. самые свежие документы с файлом за период — если кандидатов мало.
Сравнение — по основам слов с весом редких слов и по парам соседних слов. Чтение — параллельно, не больше
scan_limit документов, чтобы ответ укладывался в разумное время.
"""

import math
import re
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

from src.services.directum_client import DirectumError

DOCS = "IOfficialDocuments"
WORD = re.compile(r"[а-яёa-z]{3,}", re.I)
NAME = re.compile(r"\b([А-ЯЁ][а-яё]{2,})\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:вна|вич|ична|ич)\b")
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
STEM = 6
STOP = {w[:STEM] for w in """
обращается обращаюсь обращение обращения прошу просим пожалуйста уважаемый уважаемая здравствуйте также очень
который которая которые этого этом такой такие таких было были будет будут может могут нужно необходимо
вопрос вопросы вопросу почему когда только всего более менее после перед между через другие каждый своих свои
проработайте проработать рассмотреть рекомендую использование использованием обеспечить принять контроле контроль
""".split()}
DEFAULT_PERIOD_DAYS = 180
MAX_KEYWORDS = 6
WORKERS = 6
LIKELY = 0.35
POSSIBLE = 0.2


def stems(text: str) -> list[str]:
    out = []
    for word in WORD.findall((text or "").lower().replace("ё", "е")):
        if len(word) < 4:
            continue
        stem = word[:STEM]
        if stem not in STOP:
            out.append(stem)
    return out


def _literal(value: str) -> str:
    return value.replace("'", "''")


class ContentSearchService:
    def __init__(self, client: Any, text_service: Any, qa_search: Any | None = None, tz: tzinfo | None = None,
                 now: Callable[[], datetime] | None = None):
        self.client = client
        self.text_service = text_service
        self.qa_search = qa_search
        self.tz = tz or datetime.now().astimezone().tzinfo
        self._now = now or (lambda: datetime.now(timezone.utc))

    def search(
        self,
        text: str,
        date_from: date | None = None,
        date_to: date | None = None,
        entity_set: str = DOCS,
        kind_label: str | None = None,
        limit: int = 5,
        scan_limit: int = 40,
        use_rag: bool = True,
    ) -> dict[str, Any]:
        query = (text or "").strip()
        q_stems = stems(query)
        if len(set(q_stems)) < 2:
            raise ValueError("Слишком короткий образец: дайте хотя бы одно-два предложения из документа.")
        today = self._now().astimezone(self.tz).date()
        start = date_from or today - timedelta(days=DEFAULT_PERIOD_DAYS)
        end = date_to or today
        base = [f"Created ge {self._day_literal(start)}", f"Created lt {self._day_literal(end + timedelta(days=1))}",
                "HasVersions eq true"]
        candidates: dict[int, dict[str, Any]] = {}
        notes: list[str] = []

        def add(rows, method):
            for row in rows:
                doc_id = int(row["Id"])
                entry = candidates.setdefault(doc_id, {"id": doc_id, "name": row.get("Name") or "", "methods": []})
                if method not in entry["methods"]:
                    entry["methods"].append(method)

        # 1. RAG-области документов.
        if use_rag and self.qa_search is not None:
            add(self._rag_candidates(query, notes), "rag")
        # 2. Редкие слова образца и фамилия — в названии и теме.
        surnames = [m.group(1) for m in NAME.finditer(query)]
        keywords = self._keywords(query, surnames)
        terms = keywords + [s[:-2] if len(s) > 6 else s for s in surnames]
        if terms:
            condition = " or ".join(f"contains({field},'{_literal(t)}')" for t in terms for field in ("Name", "Subject"))
            try:
                add(self.client.query(entity_set, filter_=" and ".join(base + [f"({condition})"]), select="Id,Name",
                                      orderby="Created desc", top=scan_limit), "attributes")
            except DirectumError as exc:
                notes.append(f"Поиск по реквизитам не выполнен: {exc.safe_message}")
        # 3. Свежие документы за период — если кандидатов мало.
        if len(candidates) < scan_limit:
            room = scan_limit - len(candidates)
            add(self.client.query(entity_set, filter_=" and ".join(base), select="Id,Name", orderby="Created desc",
                                  top=max(1, room // 2)), "recent")
            modified = [f"Modified ge {self._day_literal(start)}", "HasVersions eq true"]
            add(self.client.query(entity_set, filter_=" and ".join(modified), select="Id,Name", orderby="Modified desc",
                                  top=scan_limit), "recently_modified")
        picked = list(candidates.values())[:scan_limit]
        texts = self._texts([c["id"] for c in picked])
        scored = self._score(query, q_stems, picked, texts)
        found = [s for s in scored if s["score"] >= POSSIBLE][:limit]
        message = ""
        if not found:
            message = (f"Похожих документов не найдено среди {len(picked)} проверенных за "
                       f"{start.strftime('%d.%m.%Y')}–{end.strftime('%d.%m.%Y')}"
                       + (f" (вид: {kind_label})" if kind_label else "")
                       + ". Уточните период, вид документа или автора и повторите один раз; искать перебором "
                         "формулировок бессмысленно — сравнивается текст целиком.")
        return {
            "query_terms": sorted(set(q_stems))[:30],
            "keywords": keywords, "surnames": surnames,
            "period": {"from": start.isoformat(), "to": end.isoformat()},
            "scanned": len(picked), "unreadable": sum(1 for c in picked if not texts.get(c["id"])),
            "items": found, "message": message, "notes": notes,
        }

    # --- кандидаты ---

    def _rag_candidates(self, query: str, notes: list[str]) -> list[dict[str, Any]]:
        try:
            areas = self._document_areas()
            if not areas:
                return []
            answer = self.qa_search.ask(query, area_ids=areas, wait_seconds=40)
        except Exception as exc:  # noqa: BLE001 — RAG вспомогательный, без него поиск продолжается
            notes.append(f"Смысловой поиск RX (RAG) недоступен: {exc}")
            return []
        if not getattr(answer, "score", 0):
            return []
        rows = []
        for source in getattr(answer, "sources", None) or []:
            match = re.search(r"[?&]id=(\d+)", getattr(source, "url", "") or "")
            if match:
                rows.append({"Id": int(match.group(1)), "Name": getattr(source, "name", "") or ""})
        return rows

    def _document_areas(self) -> list[int]:
        ready = {int(a.id) for a in self.qa_search.areas()}
        rows = self.client.query("IQASearchAreaDocuments", filter_="Status eq 'Active' and IsIndexed eq true",
                                 select="Id", top=50)
        return sorted(ready & {int(r["Id"]) for r in rows})

    @staticmethod
    def _keywords(query: str, surnames: list[str]) -> list[str]:
        """Различительные слова образца для поиска в названии и теме: аббревиатуры (ГИБДД, МЧС) — первыми, затем
        самые длинные слова; ФИО из образца и служебные слова не берутся."""
        name_words = {w.lower() for m in NAME.finditer(query) for w in m.group(0).split()}
        abbreviations = list(dict.fromkeys(a for a in re.findall(r"\b[А-ЯЁA-Z]{3,}\b", query)))
        words = []
        for word in WORD.findall(query):
            low = word.lower().replace("ё", "е")
            if len(low) < 6 or low in name_words or low[:STEM] in STOP or word in abbreviations:
                continue
            words.append(low)
        long_words = sorted(dict.fromkeys(words), key=lambda w: (-len(w), w))
        stems_out = [w[:STEM] for w in long_words]
        return list(dict.fromkeys(abbreviations + stems_out))[:MAX_KEYWORDS]

    def _texts(self, ids: list[int]) -> dict[int, str]:
        def read(doc_id):
            try:
                return doc_id, self.text_service.get_text(doc_id, max_chars=20000).text or ""
            except DirectumError:
                return doc_id, ""

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            return dict(pool.map(read, ids))

    # --- сравнение ---

    def _score(self, query: str, q_stems: list[str], picked: list[dict[str, Any]], texts: dict[int, str]) -> list[dict[str, Any]]:
        doc_stems = {c["id"]: set(stems(texts.get(c["id"], "") + " " + c["name"])) for c in picked}
        n = len(picked) + 1
        df = Counter(s for st in doc_stems.values() for s in st)
        q_set = set(q_stems)
        weight = {s: math.log(n / (1 + df.get(s, 0))) + 1 for s in q_set}
        total = sum(weight.values()) or 1.0
        q_pairs = set(zip(q_stems, q_stems[1:]))
        results = []
        for c in picked:
            body = texts.get(c["id"], "")
            if not body:
                continue
            ds = doc_stems[c["id"]]
            matched = q_set & ds
            share = sum(weight[s] for s in matched) / total
            d_list = stems(body)
            pairs = len(q_pairs & set(zip(d_list, d_list[1:]))) / (len(q_pairs) or 1)
            score = round(0.75 * share + 0.25 * pairs, 3)
            results.append({
                "id": c["id"], "name": c["name"], "score": score,
                "confidence": "высокая" if score >= LIKELY else "средняя" if score >= POSSIBLE else "низкая",
                "matched_terms": sorted(matched, key=lambda s: -weight[s])[:10],
                "snippet": self._snippet(body, q_set), "found_by": c["methods"],
                "url": self.client.build_document_card_url(c["id"]) if hasattr(self.client, "build_document_card_url") else None,
            })
        results.sort(key=lambda r: (-r["score"], -r["id"]))
        return results

    @staticmethod
    def _snippet(body: str, q_set: set) -> str:
        best, best_hits = "", 0
        for sentence in SENTENCE.split(body):
            hits = len(q_set & set(stems(sentence)))
            if hits > best_hits:
                best, best_hits = sentence.strip(), hits
        best = re.sub(r"\s+", " ", best)
        return best if len(best) <= 300 else best[:299].rstrip() + "…"

    def _day_literal(self, day: date) -> str:
        return datetime(day.year, day.month, day.day, tzinfo=self.tz).isoformat()
