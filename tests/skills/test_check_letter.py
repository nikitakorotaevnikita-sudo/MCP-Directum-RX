import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "docs" / "usecases" / "skills" / "og-letters"
SCRIPT = SKILL / "scripts" / "check_letter.py"

_spec = importlib.util.spec_from_file_location("check_letter", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["check_letter"] = mod
_spec.loader.exec_module(mod)

LONG = " ".join(["слово"] * 45)
REQUEST = {"registration_number": "140-ОГ", "registration_date": "2026-09-22", "deadline": "2026-10-22",
           "initial_deadline": "2026-10-22", "prolongations": [],
           "questions": [{"question": "Ремонт дорог", "code": "0003"}, {"question": "Уличное освещение", "code": "0005"}]}


def run_cli(*args, stdin=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def codes(text, kind="answer", request=None):
    return [i["code"] for i in mod.check(text, kind, request)["issues"]]


def test_clean_answer():
    text = (f"Ваше обращение от 22.09.2026 № 140-ОГ рассмотрено. Сообщаем, что ремонт дорог и уличное освещение "
            f"включены в план. {LONG}")
    assert codes(text, request=REQUEST) == []


def test_placeholders():
    text = f"ЧЕРНОВИК — проверить\n[дата] ___ ХХ.ХХ.ХХХХ ДД.ММ.ГГГГ рассмотрено {LONG}"
    res = mod.check(text)
    ph = [i for i in res["issues"] if i["code"] == "placeholder"]
    assert len(ph) == 5 and ph[0]["line"] == 1 and all(i["severity"] == "violation" for i in ph)


def test_personal_data():
    text = (f"рассмотрено. Телефон +7 (912) 345-67-89, 8-800-555-35-35, почта a.b@mail.ru, СНИЛС 123-456-789 00, "
            f"паспорт серии 45 06 123456. {LONG}")
    pii = [i["message"] for i in mod.check(text)["issues"] if i["code"] == "personal_data"]
    assert len(pii) == 5
    # Номер документа и дата — не телефон.
    assert "personal_data" not in codes(f"рассмотрено письмо № 12345678901 от 22.09.2026 {LONG}")


def test_required_elements_by_kind():
    assert "missing_result" in codes(f"Текст без итога. {LONG}")
    assert set(codes("Срок будет другим.", "prolongation")) >= {"missing_prolonged", "missing_reason", "new_deadline_missing"}
    assert codes("Ваше обращение направлено в министерство по компетенции.", "redirect") == []
    assert set(codes("Письмо.", "cover")) == {"missing_sending", "missing_competence", "missing_attachment", "missing_notify"}
    assert codes("Прошу представить сведения в течение 15 дней.", "agency") == []
    assert "missing_term" in codes("Прошу представить сведения.", "agency")


def test_too_short_answer():
    assert "too_short" in codes("Обращение рассмотрено.")
    assert "too_short" not in codes("Обращение рассмотрено.", "redirect")


def test_request_reference_and_coverage():
    text = f"Сообщаем, что ремонт дорог запланирован. {LONG}"
    res = mod.check(text, "answer", {"items": [REQUEST]})
    got = [i["code"] for i in res["issues"]]
    assert "request_number_missing" in got and "request_date_missing" in got
    assert got.count("question_not_addressed") == 1
    assert res["summary"]["questions_total"] == 2 and res["summary"]["questions_covered"] == 1
    # Номер в другом написании засчитывается.
    assert "request_number_missing" not in codes(f"№ 140 - ОГ рассмотрено {LONG}", request=REQUEST)


def test_question_with_only_stopwords_is_covered():
    req = dict(REQUEST, questions=[{"question": "Другие вопросы", "code": None}])
    assert "question_not_addressed" not in codes(f"№ 140-ОГ от 22.09.2026 рассмотрено {LONG}", request=req)


def test_prolongation_limits():
    base = "Рассмотрение обращения № 140-ОГ от 22.09.2026 продлено до {} в связи с запросом сведений."
    assert codes(base.format("15.11.2026"), "prolongation", REQUEST) == []
    assert "prolongation_over_30" in codes(base.format("25.11.2026"), "prolongation", REQUEST)
    late = dict(REQUEST, initial_deadline="2026-11-20", deadline="2026-11-20")
    assert "total_over_60" in codes(base.format("25.11.2026"), "prolongation", late)
    assert "new_deadline_missing" in codes(base.format("20.10.2026"), "prolongation", REQUEST)
    word = "Рассмотрение обращения № 140-ОГ от 22 сентября 2026 г. продлено до 15 ноября 2026 г. в связи с проверкой."
    assert codes(word, "prolongation", REQUEST) == []
    again = dict(REQUEST, prolongations=[{"number": 1}])
    assert "already_prolonged" in codes(base.format("15.11.2026"), "prolongation", again)


def test_invalid_dates_ignored():
    assert mod.dates_in("31.02.2026 и 30 февраля 2026") == []


def test_bad_inputs():
    for args in (("текст", "unknown", None), ("текст", "answer", {"items": []}), ("текст", "answer", "строка")):
        try:
            mod.check(*args)
        except mod.InputError:
            pass
        else:
            raise AssertionError(args)


def test_large_text_is_fast():
    text = ("Сообщаем, что обращение рассмотрено [поле] 8 800 555 35 35. " * 5000)[: mod.MAX_CHARS]
    t0 = time.perf_counter()
    mod.check(text, "answer", REQUEST)
    assert time.perf_counter() - t0 < 10


def test_example_matches_expected():
    text = (SKILL / "examples" / "answer.txt").read_text(encoding="utf-8")
    request = json.loads((SKILL / "examples" / "request.json").read_text(encoding="utf-8"))
    expected = json.loads((SKILL / "examples" / "answer.expected.json").read_text(encoding="utf-8"))
    assert mod.check(text, "answer", request) == expected


def test_example_prolongation_is_clean():
    text = (SKILL / "examples" / "prolongation.txt").read_text(encoding="utf-8")
    request = json.loads((SKILL / "examples" / "request.json").read_text(encoding="utf-8"))
    assert mod.check(text, "prolongation", request)["issues"] == []


def test_cli(tmp_path):
    letter = tmp_path / "l.txt"
    letter.write_bytes(f"Обращение № 140-ОГ рассмотрено [дата]. {LONG}".encode("cp1251"))
    request = tmp_path / "r.json"
    request.write_text(json.dumps(REQUEST, ensure_ascii=False), encoding="utf-8")
    code, out, _ = run_cli(str(letter), "--request", str(request))
    assert code == 0 and json.loads(out)["summary"]["violations"] == 1
    code, out, _ = run_cli(str(letter), "--kind", "pos", "--format", "md")
    assert code == 0 and "ответ в ПОС" in out
    code, _, err = run_cli("-", "--request", "-")
    assert code == 2 and "stdin" in err
    code, _, err = run_cli(str(letter), "--request", str(tmp_path / "нет.json"))
    assert code == 2 and "не найден" in err
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    code, _, err = run_cli(str(letter), "--request", str(bad))
    assert code == 2 and "не JSON" in err
    code, _, err = run_cli("-", stdin=b"  ")
    assert code == 2 and "пустой" in err
    code, _, err = run_cli(str(letter), "--kind", "x")
    assert code == 2 and "Ошибка аргументов" in err
