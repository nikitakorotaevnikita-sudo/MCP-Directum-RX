"""Проверка качества ответов на обращения граждан по стандарту (standard/standard.json).

Вход — ответ mcpOGV list_citizen_request_answers (объект с answers), список ответов или один ответ; можно склеить
несколько страниц в список. Стандарт — JSON-файл навыка (--standard): фразы-признаки (регулярные выражения),
пороги, правила вердикта. Скрипт вырезает тело ответа из бланка (от обращения «Уважаемый…» до подписи) и проверяет
критерии: отписка, формализм, ответ на каждый вопрос, конкретика, соответствие результату рассмотрения,
оформление, срок, персональные данные. Каждое замечание — с цитатой. Итог — вердикт по ответу и сводка по
исполнителям и тематикам.

Запуск:  python review_answers.py ОТВЕТЫ.json|- [--standard standard.json] [--format json|md|xlsx-spec]
                                  [--examples 5] [--debug]
Коды выхода: 0 — успех, 2 — ошибка входных данных или стандарта, 1 — непредвиденная ошибка.
"""

import argparse
import json
import re
import sys
import traceback
from collections import Counter
from pathlib import Path

DEFAULT_STANDARD = Path(__file__).resolve().parents[1] / "standard" / "standard.json"
MAX_BYTES = 50_000_000
MAX_ANSWERS = 2000
QUOTE = 220
EXCERPT = 300
WORD = re.compile(r"[а-яёa-z]+", re.I)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z«\"])")
CRITERIA = ["otpiska", "formalism", "coverage", "specificity", "result_consistency", "form", "timeliness", "personal_data"]
STATUS_ORDER = {"violation": 0, "remark": 1, "ok": 2, "n/a": 3}
STATUS_LABEL = {"violation": "нарушение", "remark": "замечание", "ok": "норма", "n/a": "—"}
DISCLAIMER = ("Проверка по формальным признакам стандарта. Признаки — повод перечитать ответ, а не окончательный вывод: "
              "решение «отписка / по существу» принимает проверяющий по тексту.")


class InputError(Exception):
    """Ошибка входных данных или стандарта (код выхода 2)."""


# --- стандарт ---

def _compile(pattern, where):
    try:
        return re.compile(pattern, re.I | re.M)
    except (re.error, TypeError) as exc:
        raise InputError(f"стандарт: {where}: неверное регулярное выражение «{pattern}»: {exc}") from None


def load_standard(data) -> dict:
    if not isinstance(data, dict):
        raise InputError("стандарт: ожидается JSON-объект")
    crit = data.get("criteria")
    if not isinstance(crit, dict):
        raise InputError("стандарт: нет раздела criteria")
    for key in CRITERIA:
        if key not in crit or not isinstance(crit[key], dict):
            raise InputError(f"стандарт: criteria.{key} отсутствует (раздел можно отключить полем \"enabled\": false)")
    body = data.get("body") or {}
    std = {
        "name": data.get("name") or "без названия", "version": str(data.get("version") or "—"),
        "start": [_compile(p, "body.start_patterns") for p in body.get("start_patterns") or []],
        "end": [_compile(p, "body.end_patterns") for p in body.get("end_patterns") or []],
        "min_words": int(body.get("min_words") or 0),
        "short_weight": float(body.get("short_answer_weight", 2)),
        "crit": crit, "verdicts": data.get("verdicts") or [], "default": data.get("default_verdict") or "По существу",
        "bad": set(data.get("bad_verdicts") or []),
    }
    c = crit
    std["otpiska"] = [(_compile(p["re"], "otpiska.patterns"), float(p.get("weight", 1)), p.get("hint") or p["re"])
                      for p in c["otpiska"].get("patterns") or []]
    std["kanc"] = [_compile(p, "formalism.kanceliarisms") for p in c["formalism"].get("kanceliarisms") or []]
    std["nominal"] = _compile(c["formalism"].get("nominal_suffix") or r"$^", "formalism.nominal_suffix")
    std["spec"] = [(_compile(p["re"], "specificity.patterns"), p.get("kind") or "факт") for p in c["specificity"].get("patterns") or []]
    rc = c["result_consistency"]
    for key in ("action_patterns", "reason_patterns", "appeal_patterns", "explanation_patterns"):
        std[key] = [_compile(p, f"result_consistency.{key}") for p in rc.get(key) or []]
    f = c["form"]
    std["placeholders"] = [_compile(p, "form.placeholder_patterns") for p in f.get("placeholder_patterns") or []]
    std["informal"] = [_compile(p, "form.informal_patterns") for p in f.get("informal_patterns") or []]
    std["greeting"] = re.compile(f.get("greeting_pattern") or r"$^", re.M) if f.get("greeting_pattern") else None
    std["pd"] = [_compile(p, "personal_data.patterns") for p in c["personal_data"].get("patterns") or []]
    for v in std["verdicts"]:
        if not isinstance(v, dict) or not v.get("label"):
            raise InputError("стандарт: каждое правило в verdicts должно иметь label")
        for k in ("any_violation", "all_violations", "any_remark"):
            unknown = set(v.get(k) or []) - set(CRITERIA)
            if unknown:
                raise InputError(f"стандарт: verdicts «{v['label']}»: неизвестные критерии {sorted(unknown)}")
    return std


# --- текст ---

def split_body(text: str, std: dict) -> tuple[str, str, str, list[str]]:
    """(тело без строки обращения, строка обращения, область от обращения до конца, заметки)."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    notes = []
    starts = [i for i, line in enumerate(lines) if any(rx.search(line) for rx in std["start"])]
    if not starts:
        notes.append("Не найдено обращение к заявителю («Уважаемый…») — проверено всё письмо.")
        start = 0
    else:
        # Берём последнее обращение перед текстом: в бланке бывает «Уважаемая (ый) ___!» и следом настоящее.
        start = starts[0]
        for i in starts[1:]:
            between = [ln for ln in lines[start + 1:i] if ln.strip()]
            if not between:
                start = i
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if any(rx.search(lines[i]) for rx in std["end"]):
            end = i
            break
    greeting = lines[start].strip() if starts else ""
    body_lines = lines[start + 1 if starts else 0:end]
    body = "\n".join(ln.strip() for ln in body_lines if ln.strip())
    tail = "\n".join(lines[start:])
    return body, greeting, tail, notes


def sentences(body: str) -> list[str]:
    flat = re.sub(r"\s+", " ", body).strip()
    return [s for s in SENTENCE_END.split(flat) if s.strip()] if flat else []


def quote(body: str, match: re.Match) -> str:
    flat = body
    a = max(flat.rfind(".", 0, match.start()), flat.rfind("\n", 0, match.start())) + 1
    b_candidates = [i for i in (flat.find(".", match.end()), flat.find("\n", match.end())) if i != -1]
    b = min(b_candidates) + 1 if b_candidates else len(flat)
    q = re.sub(r"\s+", " ", flat[a:b]).strip()
    return q if len(q) <= QUOTE else q[:QUOTE - 1].rstrip() + "…"


def stems(text: str, stop: set) -> set:
    return {w.lower().replace("ё", "е")[:5] for w in WORD.findall(text or "") if len(w) >= 4 and w.lower() not in stop}


# --- критерии ---

def _result(status, metrics=None, evidence=None, findings=None):
    return {"status": status, "metrics": metrics or {}, "evidence": (evidence or [])[:3], "findings": findings or []}


def check_otpiska(body, words, std):
    cfg = std["crit"]["otpiska"]
    score, evidence, findings = 0.0, [], []
    for rx, weight, hint in std["otpiska"]:
        m = rx.search(body)
        if m:
            score += weight
            findings.append(hint)
            evidence.append(quote(body, m))
    if std["min_words"] and words < std["min_words"]:
        score += std["short_weight"]
        findings.append(f"ответ короче {std['min_words']} слов ({words}) — по существу ничего не сказано")
    status = "violation" if score >= cfg.get("violation_score", 3) else "remark" if score >= cfg.get("remark_score", 1) else "ok"
    return _result(status, {"score": score}, evidence, findings)


def check_formalism(body, words, std):
    cfg = std["crit"]["formalism"]
    found, evidence = [], []
    for rx in std["kanc"]:
        for m in rx.finditer(body):
            found.append(m.group(0).lower())
            if len(evidence) < 3:
                evidence.append(quote(body, m))
    sents = sentences(body)
    lens = [len(WORD.findall(s)) for s in sents]
    avg = round(sum(lens) / len(lens), 1) if lens else 0
    per_1000 = round(len(found) * 1000 / words, 1) if words else 0
    nominal = round(len(std["nominal"].findall(body)) * 1000 / words, 1) if words else 0
    long_n = sum(1 for n in lens if n >= cfg.get("long_sentence_words", 40))
    status = "ok"
    if per_1000 >= cfg.get("per_1000_violation", 45) or avg >= cfg.get("avg_sentence_words_violation", 30):
        status = "violation"
    elif per_1000 >= cfg.get("per_1000_remark", 25) or avg >= cfg.get("avg_sentence_words_remark", 22) or long_n:
        status = "remark"
    findings = []
    if found:
        findings.append("канцеляризмы: " + ", ".join(f"{k} ({v})" for k, v in Counter(found).most_common(5)))
    if long_n:
        findings.append(f"длинных предложений: {long_n}")
    return _result(status, {"kanceliarisms_per_1000": per_1000, "nominal_per_1000": nominal, "avg_sentence_words": avg,
                            "long_sentences": long_n, "sentences": len(sents)}, evidence, findings)


def check_coverage(body, answer, std):
    cfg = std["crit"]["coverage"]
    stop = {w.lower() for w in cfg.get("stop_words") or []}
    questions = []
    for req in answer.get("requests") or []:
        for q in req.get("questions") or []:
            name = (q.get("question") or "").strip()
            if name and name not in questions:
                questions.append(name)
    if not questions:
        return _result("n/a", findings=["нет вопросов обращения — полноту не проверить"])
    body_stems = stems(body, stop)
    rows, missing = [], []
    for name in questions:
        qs = stems(name, stop)
        share = round(len(qs & body_stems) / len(qs), 2) if qs else None
        covered = share is None or share >= cfg.get("min_share", 0.5)
        rows.append({"question": name, "share": share, "covered": covered})
        if not covered:
            missing.append(name)
    if not missing:
        status = "ok"
    elif len(missing) == len(questions):
        status = "violation" if cfg.get("none_covered_is_violation", True) else "remark"
    else:
        status = "violation" if cfg.get("partial_is_violation", False) else "remark"
    if missing and len(missing) == len(questions):
        missing_note = ["не освещён ни один вопрос обращения — текст не по теме или без содержания"]
    else:
        missing_note = []
    return _result(status, {"questions": len(questions), "covered": len(questions) - len(missing), "per_question": rows},
                   findings=missing_note + [f"не освещён вопрос «{m}»" for m in missing])


def check_specificity(body, answer, std):
    cfg = std["crit"]["specificity"]
    kinds, evidence = {}, []
    for rx, kind in std["spec"]:
        m = rx.search(body)
        if m and kind not in kinds:
            kinds[kind] = m.group(0)
            evidence.append(quote(body, m))
    n = len(kinds)
    status = "violation" if n < cfg.get("min_kinds_violation", 1) else "remark" if n < cfg.get("min_kinds_remark", 2) else "ok"
    codes = {answer.get("result")} | {q.get("review_result") for r in answer.get("requests") or [] for q in r.get("questions") or []}
    codes.discard(None)
    soft = set(cfg.get("soft_for_results") or [])
    if status == "violation" and codes and codes <= soft:
        status = "remark"  # разъяснению даты и суммы не обязательны
    findings = [f"есть: {', '.join(kinds)}"] if kinds else ["нет дат, сроков, объёмов, адресов и конкретных действий"]
    return _result(status, {"kinds": sorted(kinds)}, evidence, findings)


def check_result(body, answer, std):
    cfg = std["crit"]["result_consistency"]
    codes = {answer.get("result")} | {q.get("review_result") for r in answer.get("requests") or [] for q in r.get("questions") or []}
    codes.discard(None)
    if not codes:
        return _result("n/a", findings=["результат рассмотрения не указан"])
    findings, status = [], "ok"

    def has(key):
        return any(rx.search(body) for rx in std[key])

    if codes & set(cfg.get("action_results") or []) and not has("action_patterns"):
        status = "violation"
        findings.append("результат «Меры приняты / Поддержано», но в тексте нет конкретных мер")
    if codes & set(cfg.get("refusal_results") or []):
        if not has("reason_patterns"):
            status = "violation"
            findings.append("отказ без основания")
        if not has("appeal_patterns"):
            status = "violation" if status == "violation" else "remark"
            findings.append("при отказе не разъяснён порядок обжалования")
    if codes & set(cfg.get("explained_results") or []) and not has("explanation_patterns"):
        status = "violation" if status == "violation" else "remark"
        findings.append("результат «Разъяснено», но разъяснения в тексте не видно")
    return _result(status, {"results": sorted(codes)}, findings=findings)


def check_form(tail, greeting, std):
    found, evidence, status = [], [], "ok"
    for rx in std["placeholders"]:
        for m in rx.finditer(tail):
            found.append(m.group(0))
            if len(evidence) < 3:
                evidence.append(re.sub(r"\s+", " ", m.group(0))[:QUOTE])
    informal = [m.group(0) for rx in std["informal"] for m in rx.finditer(tail)]
    findings = []
    if found:
        status = "violation"
        findings.append(f"остатки шаблона: {len(found)}")
    if informal:
        status = "violation"
        findings.append("недопустимая лексика: " + ", ".join(dict.fromkeys(informal)))
        evidence.extend(informal[:2])
    if std["greeting"] is not None and not std["greeting"].search(greeting or ""):
        if status == "ok":
            status = "remark"
        findings.append("нет обращения к заявителю по имени и отчеству")
    return _result(status, {"placeholders": len(found)}, evidence, findings)


def check_timeliness(answer, std):
    cfg = std["crit"]["timeliness"]
    late = [r.get("days_late") for r in answer.get("requests") or [] if isinstance(r.get("days_late"), int)]
    if not late:
        return _result("n/a", findings=["нет данных о сроке"])
    worst = max(late)
    status = "violation" if worst >= cfg.get("violation_days", 1) else "remark" if worst > cfg.get("remark_days", 0) else "ok"
    return _result(status, {"days_late": worst}, findings=[f"ответ позже срока на {worst} дн."] if worst > 0 else [])


def check_pd(body, std):
    hits = [m for rx in std["pd"] for m in rx.finditer(body)]
    if not hits:
        return _result("ok")
    return _result("remark", {"count": len(hits)}, findings=["контакты или документы в тексте ответа — проверьте, нужны ли"])


def assess(answer: dict, std: dict) -> dict:
    text = answer.get("text") or ""
    base = {"id": answer.get("id"), "registration_number": answer.get("registration_number"),
            "registration_date": answer.get("registration_date"), "url": answer.get("url"),
            "prepared_by": answer.get("prepared_by") or "не указан", "signatory": answer.get("signatory"),
            "requests": [r.get("registration_number") for r in answer.get("requests") or []],
            "topics": sorted({".".join((q.get("code") or "").split(".")[:2]) for r in answer.get("requests") or []
                              for q in r.get("questions") or [] if q.get("code")})}
    if not text.strip():
        return {**base, "verdict": "Нет текста", "checked": False,
                "notes": [answer.get("text_message") or "текст письма не получен"], "criteria": {}}
    body, greeting, tail, notes = split_body(text, std)
    words = len(WORD.findall(body))
    crit = std["crit"]
    checks = {
        "otpiska": lambda: check_otpiska(body, words, std),
        "formalism": lambda: check_formalism(body, words, std),
        "coverage": lambda: check_coverage(body, answer, std),
        "specificity": lambda: check_specificity(body, answer, std),
        "result_consistency": lambda: check_result(body, answer, std),
        "form": lambda: check_form(tail, greeting, std),
        "timeliness": lambda: check_timeliness(answer, std),
        "personal_data": lambda: check_pd(body, std),
    }
    results = {}
    for key in CRITERIA:
        if crit[key].get("enabled", True) is False:
            continue
        results[key] = {"label": crit[key].get("label") or key, **checks[key]()}
    verdict = std["default"]
    viol = {k for k, r in results.items() if r["status"] == "violation"}
    rem = {k for k, r in results.items() if r["status"] == "remark"}
    for rule in std["verdicts"]:
        if ((rule.get("any_violation") and viol & set(rule["any_violation"]))
                or (rule.get("all_violations") and set(rule["all_violations"]) <= viol)
                or (rule.get("any_remark") and (rem | viol) & set(rule["any_remark"]))):
            verdict = rule["label"]
            break
    reasons = [f"{results[k]['label']}: {'; '.join(results[k]['findings'])}" for k in sorted(viol, key=CRITERIA.index)
               if results[k]["findings"]]
    remarks = [f"{results[k]['label']}: {'; '.join(results[k]['findings'])}" for k in sorted(rem, key=CRITERIA.index)
               if results[k]["findings"]]
    flat = re.sub(r"\s+", " ", body).strip()
    excerpt = flat if len(flat) <= EXCERPT else flat[:EXCERPT - 1].rstrip() + "…"
    return {**base, "verdict": verdict, "checked": True, "words": words, "notes": notes, "reasons": reasons,
            "remarks": remarks, "excerpt": excerpt, "criteria": results}


def extract(data) -> list:
    if isinstance(data, dict):
        if isinstance(data.get("answers"), list):
            data = data["answers"]
        elif "id" in data or "text" in data:
            data = [data]
        else:
            raise InputError("ожидался ответ list_citizen_request_answers (поле answers), список ответов или один ответ")
    if not isinstance(data, list):
        raise InputError("ожидался список ответов")
    items = []
    for item in data:  # можно передать список страниц
        if isinstance(item, dict) and isinstance(item.get("answers"), list):
            items.extend(a for a in item["answers"] if isinstance(a, dict))
        elif isinstance(item, dict):
            items.append(item)
    if not items:
        raise InputError("в данных нет ни одного ответа")
    if len(items) > MAX_ANSWERS:
        raise InputError(f"слишком много ответов: {len(items)} (допустимо не более {MAX_ANSWERS})")
    seen, unique = set(), []
    for a in items:
        key = a.get("id") or id(a)
        if key not in seen:
            seen.add(key)
            unique.append(a)
    return unique


def review(data, standard: dict, standard_path: str | None = None) -> dict:
    std = load_standard(standard)
    rows = [assess(a, std) for a in extract(data)]
    checked = [r for r in rows if r["checked"]]
    bad = [r for r in checked if r["verdict"] in std["bad"]]

    def group(key_fn):
        groups = {}
        for r in checked:
            for key in key_fn(r):
                g = groups.setdefault(key, {"key": key, "answers": 0, "bad": 0, "verdicts": Counter()})
                g["answers"] += 1
                g["verdicts"][r["verdict"]] += 1
                if r["verdict"] in std["bad"]:
                    g["bad"] += 1
        out = []
        for g in groups.values():
            out.append({"key": g["key"], "answers": g["answers"], "bad": g["bad"],
                        "bad_share": round(g["bad"] / g["answers"], 2), "verdicts": dict(g["verdicts"].most_common())})
        return sorted(out, key=lambda g: (-g["bad_share"], -g["answers"], g["key"]))

    violations = Counter(k for r in checked for k, c in r["criteria"].items() if c["status"] == "violation")
    return {
        "standard": {"name": std["name"], "version": std["version"],
                     "file": Path(standard_path).name if standard_path and standard_path != "-" else None},
        "summary": {
            "answers": len(rows), "checked": len(checked), "without_text": len(rows) - len(checked),
            "bad": len(bad), "bad_share": round(len(bad) / len(checked), 2) if checked else None,
            "verdicts": dict(Counter(r["verdict"] for r in rows).most_common()),
            "violations_by_criterion": {std["crit"][k].get("label", k): n for k, n in violations.most_common()},
        },
        "by_prepared_by": group(lambda r: [r["prepared_by"]]),
        "by_topic": group(lambda r: r["topics"] or ["без классификации"]),
        "answers": sorted(rows, key=lambda r: (r["verdict"] not in std["bad"], r["registration_date"] or "")),
        "disclaimer": DISCLAIMER,
    }


# --- вывод ---

def _cell(v):
    return ("—" if v in (None, "", []) else str(v)).replace("|", "\\|").replace("\n", " ")


def _link(r):
    num = r.get("registration_number") or f"id {r.get('id')}"
    return f"[{num}]({r['url']})" if r.get("url") else num


def to_markdown(res: dict, examples: int = 5) -> str:
    s, st = res["summary"], res["standard"]
    out = [f"# Качество ответов на обращения", "", f"Стандарт: {st['name']}, версия {st['version']}.", "",
           f"Ответов: **{s['answers']}**, проверено: **{s['checked']}**, без текста: {s['without_text']}. "
           f"С проблемами: **{s['bad']}**" + (f" ({round(s['bad_share'] * 100)} %)." if s["bad_share"] is not None else "."), ""]
    out += ["| Вердикт | Ответов |", "|---|---|"] + [f"| {_cell(k)} | {v} |" for k, v in s["verdicts"].items()] + [""]
    if s["violations_by_criterion"]:
        out += ["Нарушения по критериям: " + ", ".join(f"{k} — {v}" for k, v in s["violations_by_criterion"].items()) + ".", ""]
    bad = [r for r in res["answers"] if r["checked"] and r["reasons"]]
    if bad:
        out += ["## Ответы с проблемами", "", "| Ответ | Дата | Подготовил | Вердикт | Почему |", "|---|---|---|---|---|"]
        for r in bad:
            out.append(f"| {_cell(_link(r))} | {_cell(r['registration_date'])} | {_cell(r['prepared_by'])} | "
                       f"{_cell(r['verdict'])} | {_cell(' / '.join(r['reasons']))} |")
        out.append("")
        out += ["## Примеры (цитаты)", ""]
        for r in bad[:examples]:
            out.append(f"**{_link(r)}** — {r['verdict']}")
            out.append(f"- Начало ответа: «{r['excerpt']}»")
            for key, c in r["criteria"].items():
                if c["status"] == "violation" and c["evidence"]:
                    out.append(f"- {c['label']}: «{c['evidence'][0]}»")
            out.append("")
    for title, key in (("По исполнителям", "by_prepared_by"), ("По тематикам (код ТОТК)", "by_topic")):
        if res[key]:
            out += [f"## {title}", "", "| | Ответов | С проблемами | Доля |", "|---|---|---|---|"]
            out += [f"| {_cell(g['key'])} | {g['answers']} | {g['bad']} | {round(g['bad_share'] * 100)} % |" for g in res[key]]
            out.append("")
    out.append(f"> {res['disclaimer']}")
    return "\n".join(out) + "\n"


def to_xlsx_spec(res: dict) -> dict:
    rows = []
    for r in res["answers"]:
        c = r.get("criteria") or {}
        rows.append({"number": r.get("registration_number"), "date": r.get("registration_date"), "prepared_by": r.get("prepared_by"),
                     "verdict": r.get("verdict"), **{k: STATUS_LABEL.get((c.get(k) or {}).get("status"), "—") for k in CRITERIA},
                     "reasons": " / ".join(r.get("reasons") or r.get("notes") or []), "url": r.get("url")})
    labels = {"otpiska": "Отписка", "formalism": "Формализм", "coverage": "Все вопросы", "specificity": "Конкретика",
              "result_consistency": "Результат", "form": "Оформление", "timeliness": "Срок", "personal_data": "ПДн"}
    return {"properties": {"title": "Качество ответов на обращения"}, "sheets": [
        {"name": "Ответы", "title": f"Проверка по стандарту «{res['standard']['name']}» v{res['standard']['version']}",
         "columns": [{"title": "Ответ", "key": "number", "type": "text"}, {"title": "Дата", "key": "date", "type": "date"},
                     {"title": "Подготовил", "key": "prepared_by", "type": "text", "width": 28},
                     {"title": "Вердикт", "key": "verdict", "type": "text", "width": 20}]
                    + [{"title": labels[k], "key": k, "type": "text"} for k in CRITERIA]
                    + [{"title": "Почему", "key": "reasons", "type": "text", "width": 60}, {"title": "Ссылка", "key": "url", "type": "url"}],
         "rows": rows,
         "highlight": [{"column": "verdict", "op": "==", "value": v, "color": "red", "row": True} for v in ("Отписка",)]},
        {"name": "По исполнителям", "title": "Доля ответов с проблемами по исполнителям",
         "columns": [{"title": "Исполнитель", "key": "key", "type": "text", "width": 32}, {"title": "Ответов", "key": "answers", "type": "integer"},
                     {"title": "С проблемами", "key": "bad", "type": "integer"}, {"title": "Доля", "key": "bad_share", "type": "percent"}],
         "rows": [{k: g[k] for k in ("key", "answers", "bad", "bad_share")} for g in res["by_prepared_by"]],
         "totals": {"label": "Итого", "columns": ["answers", "bad"], "func": "sum"}},
    ]}


# --- ввод-вывод ---

def read_json(path: str, label: str):
    if path == "-":
        data = sys.stdin.buffer.read()
    else:
        p = Path(path)
        if not p.is_file():
            raise InputError(f"{label}: файл не найден: {path}")
        data = p.read_bytes()
    if len(data) > MAX_BYTES:
        raise InputError(f"{label}: файл слишком большой")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if not data.strip():
        raise InputError(f"{label}: данные пустые")
    try:
        return json.loads(data.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise InputError(f"{label}: это не JSON: {exc.msg} (строка {exc.lineno})") from None


def _write(stream, s: str):
    try:
        stream.buffer.write(s.encode("utf-8"))
        stream.buffer.flush()
    except AttributeError:
        stream.write(s)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        _write(sys.stderr, f"Ошибка аргументов: {message}\n{self.format_usage()}")
        sys.exit(2)


def main(argv=None) -> int:
    parser = _Parser(description="Проверка качества ответов на обращения граждан по стандарту.")
    parser.add_argument("input", help="ответ list_citizen_request_answers (JSON), список ответов или страниц, или «-»")
    parser.add_argument("--standard", default=str(DEFAULT_STANDARD), help="файл стандарта (по умолчанию standard/standard.json навыка)")
    parser.add_argument("--format", choices=["json", "md", "xlsx-spec"], default="json", help="формат вывода")
    parser.add_argument("--examples", type=int, default=5, help="сколько примеров с цитатами в md (0–50)")
    parser.add_argument("--debug", action="store_true", help="выводить трассировку при ошибке")
    args = parser.parse_args(argv)
    try:
        if args.input == "-" and args.standard == "-":
            raise InputError("stdin можно использовать только для одного из входов")
        if not 0 <= args.examples <= 50:
            raise InputError("--examples: допустимо от 0 до 50")
        res = review(read_json(args.input, "ответы"), read_json(args.standard, "стандарт"), args.standard)
        if args.format == "md":
            out = to_markdown(res, args.examples)
        else:
            out = json.dumps(to_xlsx_spec(res) if args.format == "xlsx-spec" else res, ensure_ascii=False, indent=2) + "\n"
        _write(sys.stdout, out)
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
