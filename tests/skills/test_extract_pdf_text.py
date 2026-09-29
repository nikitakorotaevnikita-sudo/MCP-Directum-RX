import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

pypdf = pytest.importorskip("pypdf")
pytest.importorskip("reportlab")

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "docs" / "usecases" / "skills" / "pdf-read"
SCRIPT = SKILL / "scripts" / "extract_pdf_text.py"
EXAMPLE = SKILL / "examples" / "example.pdf"
FONT = ROOT / "docs" / "usecases" / "skills" / "pdf-report" / "fonts" / "DejaVuSans.ttf"

_spec = importlib.util.spec_from_file_location("extract_pdf_text", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["extract_pdf_text"] = mod
_spec.loader.exec_module(mod)


def run_cli(*args, stdin=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def make_pdf(pages, graphic_pages=()):
    """PDF из списков строк; номера страниц из graphic_pages — только графика, без текста."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(TTFont("DejaVuTest", str(FONT)))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    for index, lines in enumerate(pages, start=1):
        if index in graphic_pages:
            c.rect(72, 400, 300, 200, fill=1)
        else:
            c.setFont("DejaVuTest", 12)
            y = 800
            for line in lines:
                c.drawString(72, y, line)
                y -= 18
        c.showPage()
    c.save()
    return buf.getvalue()


def encrypt(data, password):
    reader = pypdf.PdfReader(io.BytesIO(data))
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(password)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


TEXT_PAGE = ["Проверка извлечения текста из документа", "Вторая строка с кириллицей и цифрами 2026"]


def test_example_pdf():
    res = mod.extract(EXAMPLE.read_bytes(), file_name="example.pdf")
    assert res["pages_total"] == 3 and res["pages_extracted"] == [1, 2, 3]
    assert res["has_text_layer"] and res["pages_without_text"] == [3]
    assert "благоустройства дворовых территорий" in res["pages"][0]["text"]
    assert "вступает в силу" in res["pages"][1]["text"]
    assert res["metadata"]["title"] == "Постановление о благоустройстве"
    assert res["metadata"]["author"] == "Отдел по работе с ПА"
    assert not res["encrypted"] and not res["truncated"]
    assert any("Без текстового слоя" in w for w in res["warnings"])


def test_scan_only_pdf():
    res = mod.extract(make_pdf([[], []], graphic_pages=(1, 2)))
    assert not res["has_text_layer"] and res["pages_without_text"] == [1, 2]
    assert any("OCR" in w for w in res["warnings"])
    assert "[нет текстового слоя]" in mod.to_text(res)


def test_page_selection():
    data = make_pdf([["Страница один текст длинный"], ["Страница два текст длинный"], ["Страница три текст длинный"]])
    assert mod.extract(data, pages="2")["pages_extracted"] == [2]
    assert mod.extract(data, pages="3,1-2,2")["pages_extracted"] == [3, 1, 2]
    assert mod.extract(data, pages="2-")["pages_extracted"] == [2, 3]
    assert mod.extract(data, pages="1-99")["pages_extracted"] == [1, 2, 3]
    for bad in ("0", "4", "3-1", "а", "1-2-3", ","):
        with pytest.raises(mod.InputError):
            mod.extract(data, pages=bad)


def test_parse_pages_default():
    assert mod.parse_pages(None, 3) == [1, 2, 3]
    assert mod.parse_pages("  ", 2) == [1, 2]


def test_truncation():
    data = make_pdf([TEXT_PAGE] * 5)
    res = mod.extract(data, max_chars=100)
    assert res["truncated"] and res["total_chars"] == 100
    assert res["pages_extracted"][0] == 1 and len(res["pages_extracted"]) < 5
    assert any("обрезан" in w for w in res["warnings"])


def test_encrypted():
    data = encrypt(make_pdf([TEXT_PAGE]), "секрет")
    with pytest.raises(mod.InputError, match="--password"):
        mod.extract(data)
    with pytest.raises(mod.InputError, match="неверный пароль"):
        mod.extract(data, password="не тот")
    res = mod.extract(data, password="секрет")
    assert res["encrypted"] and "кириллицей" in res["pages"][0]["text"]


def test_encrypted_with_empty_user_password_opens():
    res = mod.extract(encrypt(make_pdf([TEXT_PAGE]), ""))
    assert res["encrypted"] and res["has_text_layer"]


def test_not_pdf_and_broken():
    with pytest.raises(mod.InputError, match="не PDF"):
        mod.extract("Просто текст".encode("utf-8"))
    with pytest.raises(mod.InputError, match="пустой"):
        mod.extract(b"")
    with pytest.raises(mod.InputError):
        mod.extract(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\ngarbage garbage garbage")


def test_truncated_file_does_not_crash():
    data = make_pdf([TEXT_PAGE, TEXT_PAGE])
    try:
        res = mod.extract(data[: len(data) // 2])
    except mod.InputError:
        return
    assert isinstance(res["pages"], list)


def test_clean_text():
    assert mod.clean_text("благо-\nустройство") == "благоустройство"
    assert mod.clean_text("Санкт-\nПетербург") == "Санкт-\nПетербург"
    assert mod.clean_text("пере­нос") == "перенос"
    assert mod.clean_text("a  \t b c\r\n\r\n\r\n\r\nd\x00") == "a b c\n\nd"


def test_garbage_share():
    assert mod.garbage_share("") == 0.0
    assert mod.garbage_share("нормальный текст") == 0.0
    assert mod.garbage_share("�а") == 0.75


def test_raw_keeps_text():
    data = make_pdf([["слово  с   пробелами и длинной строкой"]])
    assert "слово" in mod.extract(data, raw=True)["pages"][0]["text"]


def test_layout_mode():
    res = mod.extract(make_pdf([TEXT_PAGE]), layout=True)
    assert "кириллицей" in res["pages"][0]["text"]


def test_page_error_is_isolated(monkeypatch):
    data = make_pdf([TEXT_PAGE, TEXT_PAGE])
    original = pypdf.PageObject.extract_text
    calls = {"n": 0}

    def flaky(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("битый поток")
        return original(self, *a, **kw)

    monkeypatch.setattr(pypdf.PageObject, "extract_text", flaky)
    res = mod.extract(data)
    assert "error" in res["pages"][0] and res["pages"][1]["has_text"]
    assert res["pages_without_text"] == []
    assert "[не удалось извлечь текст" in mod.to_text(res)


def test_missing_pypdf(monkeypatch):
    monkeypatch.setattr(mod, "_load_pypdf", lambda: None)
    with pytest.raises(RuntimeError, match="pypdf"):
        mod.extract(EXAMPLE.read_bytes())


def test_markdown():
    md = mod.to_markdown(mod.extract(EXAMPLE.read_bytes(), file_name="example.pdf"))
    assert md.startswith("# Постановление о благоустройстве") and "## Страница 3" in md and "_нет текстового слоя_" in md


def test_cli(tmp_path):
    code, out, err = run_cli(str(EXAMPLE))
    assert code == 0 and "=== Страница 1 ===" in out and err == ""
    code, out, _ = run_cli(str(EXAMPLE), "--format", "json", "--pages", "2")
    assert code == 0 and json.loads(out)["pages_extracted"] == [2]
    code, out, _ = run_cli(str(EXAMPLE), "--no-page-headers", "--pages", "1")
    assert code == 0 and "===" not in out
    code, out, _ = run_cli("-", "--format", "md", stdin=EXAMPLE.read_bytes())
    assert code == 0 and "## Страница 1" in out
    target = tmp_path / "sub" / "out.txt"
    code, out, err = run_cli(str(EXAMPLE), "--out", str(target))
    assert code == 0 and Path(out.strip()) == target.resolve() and "благоустройства" in target.read_text(encoding="utf-8")
    assert "Предупреждение" in err
    txt = tmp_path / "a.txt"
    txt.write_text("не pdf", encoding="utf-8")
    code, _, err = run_cli(str(txt))
    assert code == 2 and "не PDF" in err
    code, _, err = run_cli(str(tmp_path / "нет.pdf"))
    assert code == 2 and "не найден" in err
    code, _, err = run_cli(str(EXAMPLE), "--pages", "9")
    assert code == 2 and "--pages" in err
    code, _, err = run_cli(str(EXAMPLE), "--max-chars", "5")
    assert code == 2 and "--max-chars" in err
    code, _, err = run_cli()
    assert code == 2 and "Ошибка аргументов" in err
