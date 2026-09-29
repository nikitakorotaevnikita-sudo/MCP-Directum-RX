"""Готовность поручения к снятию с контроля — по досье get_control_dossier.

Проверяет: все поручения дерева завершены; по завершённым есть отчёт; нет нерассмотренных запросов на продление;
приёмка контролёром (есть ли задание, принято или на доработку); соблюдён ли срок. Возвращает «готово / не готово»,
блокеры, предупреждения и черновик причины снятия с контроля из фактов.

Запуск:  python close_readiness.py ДОСЬЕ.json|- [--format json|md] [--debug]
Коды выхода: 0 — успех, 2 — ошибка входных данных, 1 — непредвиденная ошибка.
"""

import argparse
import json
import re
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

MAX_BYTES = 20_000_000
DATE_RU = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$")
DONE = {"Completed"}
DISCLAIMER = "Решение о снятии с контроля принимает контролёр в Directum RX; проверка подсказывает, чего не хватает."


class InputError(Exception):
    """Ошибка входных данных (код выхода 2)."""


def parse_day(value):
    if not isinstance(value, str) or not value.strip():
        return None
    m = DATE_RU.match(value)
    try:
        if m:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        text = value.strip().replace("Z", "+00:00")
        return date.fromisoformat(text) if len(text) == 10 else datetime.fromisoformat(text).date()
    except ValueError:
        return None


def fmt(day):
    return day.strftime("%d.%m.%Y") if day else "—"


def check(dossier) -> dict:
    if not isinstance(dossier, dict) or not isinstance(dossier.get("action_item"), dict):
        raise InputError("ожидалось досье get_control_dossier (объект с action_item и tree)")
    item = dossier["action_item"]
    tree = [n for n in dossier.get("tree") or [] if isinstance(n, dict)] or [item]
    blockers, warnings, facts = [], [], []

    open_nodes = [n for n in tree if n.get("status") not in DONE and n.get("status") != "Aborted"]
    for n in open_nodes:
        who = n.get("assignee") or "исполнитель не указан"
        state = n.get("execution_state_label") or n.get("status_label") or n.get("status")
        overdue = f", просрочено на {n['days_overdue']} дн" if n.get("days_overdue") else ""
        if n.get("id") == item.get("id") and str(state).lower().startswith("на приёмке"):
            continue
        blockers.append(f"Не завершено поручение № {n.get('id')} ({who}): {state}{overdue}.")
    done = [n for n in tree if n.get("status") in DONE]
    no_report = [n for n in done if not (n.get("report") or "").strip()]
    for n in no_report:
        warnings.append(f"Поручение № {n.get('id')} ({n.get('assignee') or '—'}) завершено без отчёта — проверьте "
                        "результат во вложениях.")
    aborted = [n for n in tree if n.get("status") == "Aborted"]
    if aborted:
        warnings.append(f"Прекращено поручений: {len(aborted)} — убедитесь, что их работы учтены.")

    pending_ext = [h for h in dossier.get("deadline_history") or [] if h.get("state") == "на рассмотрении"]
    for h in pending_ext:
        blockers.append(f"Не рассмотрен запрос на продление до {fmt(parse_day(h.get('new_deadline')))} "
                        f"(поручение № {h.get('action_item_id')}).")
    accepted_ext = [h for h in dossier.get("deadline_history") or [] if h.get("state") == "одобрено"]

    acceptance = [a for a in dossier.get("acceptance") or [] if a.get("action_item_id") == item.get("id")]
    pending_acc = [a for a in acceptance if a.get("status") == "InProcess"]
    rework = [a for a in acceptance if a.get("result") == "на доработку"]
    accepted = [a for a in acceptance if a.get("result") == "принято"]
    root_state = str(item.get("execution_state_label") or "").lower()
    if pending_acc or root_state.startswith("на приёмке"):
        facts.append("Исполнитель отчитался, поручение ждёт приёмки контролёром.")
    elif accepted:
        facts.append(f"Работы приняты контролёром {accepted[-1].get('supervisor') or ''} "
                     f"{fmt(parse_day(accepted[-1].get('completed')))}.".replace("  ", " "))
    if rework and not accepted:
        warnings.append("Последняя приёмка — «на доработку»: убедитесь, что замечания устранены.")

    deadline = parse_day(item.get("deadline"))
    actual = parse_day(item.get("actual_date"))
    if deadline and actual:
        late = (actual - deadline).days
        facts.append(f"Исполнено {fmt(actual)} при сроке {fmt(deadline)}" + (f" — с нарушением срока на {late} дн." if late > 0 else " — в срок."))
    elif item.get("days_overdue"):
        warnings.append(f"Срок нарушен на {item['days_overdue']} дн. — отразите это в причине снятия.")
    if accepted_ext:
        facts.append(f"Срок переносился {len(accepted_ext)} раз(а), суммарно на "
                     f"{sum(h.get('shift_days') or 0 for h in accepted_ext)} дн.")
    reports = [n for n in done if (n.get("report") or "").strip()]
    if reports:
        facts.append(f"Отчётов об исполнении: {len(reports)}.")
    # Названия документов в причину не пишем: в них бывают ФИО заявителей и длинный текст — только количество.
    docs = [d for d in dossier.get("documents") or [] if not d.get("is_source")]
    if docs:
        facts.append(f"Документов во вложениях: {len(docs)}.")

    ready = not blockers
    reason = None
    if ready:
        reason = ("Снять с контроля в связи с исполнением. " + " ".join(facts)).strip()
    return {
        "action_item_id": item.get("id"), "url": item.get("url"), "subject": item.get("subject"),
        "ready": ready, "blockers": blockers, "warnings": warnings, "facts": facts,
        "draft_reason": reason, "disclaimer": DISCLAIMER,
    }


def to_markdown(r) -> str:
    title = f"[№ {r['action_item_id']}]({r['url']})" if r.get("url") else f"№ {r['action_item_id']}"
    out = [f"# Снятие с контроля: {title}", "", f"**{'Готово к снятию' if r['ready'] else 'Не готово к снятию'}**", ""]
    if r["blockers"]:
        out += ["Мешает снять:", ""] + [f"- {b}" for b in r["blockers"]] + [""]
    if r["warnings"]:
        out += ["Проверить:", ""] + [f"- {w}" for w in r["warnings"]] + [""]
    if r["facts"]:
        out += ["Факты:", ""] + [f"- {f}" for f in r["facts"]] + [""]
    if r["draft_reason"]:
        out += ["Черновик причины снятия:", "", f"> {r['draft_reason']}", ""]
    out.append(f"_{r['disclaimer']}_")
    return "\n".join(out) + "\n"


def read_json(path: str):
    if path == "-":
        data = sys.stdin.buffer.read()
    else:
        p = Path(path)
        if not p.is_file():
            raise InputError(f"файл не найден: {path}")
        try:
            data = p.read_bytes()
        except OSError as exc:
            raise InputError(f"не удалось прочитать файл {path}: {exc}") from None
    if len(data) > MAX_BYTES:
        raise InputError("файл слишком большой")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if not data.strip():
        raise InputError("данные пустые")
    try:
        return json.loads(data.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise InputError(f"это не JSON: {exc.msg} (строка {exc.lineno})") from None


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
    parser = _Parser(description="Готовность поручения к снятию с контроля.")
    parser.add_argument("input", help="досье get_control_dossier (JSON) или «-»")
    parser.add_argument("--format", choices=["json", "md"], default="json", help="формат вывода")
    parser.add_argument("--debug", action="store_true", help="выводить трассировку при ошибке")
    args = parser.parse_args(argv)
    try:
        result = check(read_json(args.input))
        out = to_markdown(result) if args.format == "md" else json.dumps(result, ensure_ascii=False, indent=2) + "\n"
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
