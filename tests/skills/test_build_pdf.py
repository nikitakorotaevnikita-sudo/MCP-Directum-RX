import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "docs" / "usecases" / "skills" / "pdf-report"
SCRIPT = SKILL / "scripts" / "build_pdf.py"

_spec = importlib.util.spec_from_file_location("build_pdf", SCRIPT)
build_pdf = importlib.util.module_from_spec(_spec)
sys.modules["build_pdf"] = build_pdf
_spec.loader.exec_module(build_pdf)


def run_cli(spec, output, stdin=False):
    args = [sys.executable, str(SCRIPT), "-" if stdin else str(spec), str(output)]
    payload = json.dumps(spec, ensure_ascii=False).encode("utf-8") if stdin else None
    proc = subprocess.run(args, input=payload, capture_output=True, timeout=300)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def write_spec(tmp_path, spec, name="spec.json"):
    path = tmp_path / name
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def text_of(path):
    return "\n".join(page.extract_text() for page in PdfReader(str(path)).pages)


def link_uris(path):
    uris = []
    for page in PdfReader(str(path)).pages:
        for annot in page.get("/Annots") or []:
            annot = annot.get_object()
            if annot.get("/Subtype") == "/Link" and "/A" in annot:
                uris.append(annot["/A"].get("/URI"))
    return uris


def long_report():
    rows = [
        [f"Поручение №{i}: подготовить справку о ходе исполнения мероприятий", "2026-09-28T00:00:00+04:00", i,
         "Петров П. П.", {"text": "Открыть", "url": f"https://rx.example.ru/card/{i}"}]
        for i in range(300)
    ]
    return {
        "title": "Отчёт о просроченных поручениях",
        "subtitle": "Проверка кириллицы",
        "author": "Аналитический отдел",
        "date": "2026-09-28",
        "orientation": "landscape",
        "blocks": [
            {"type": "heading", "level": 1, "text": "Сводка"},
            {"type": "paragraph", "text": "Текст с **жирным** и [ссылкой](https://example.gov.ru/doc?a=1&b=2) <тег> & символы"},
            {"type": "key_value", "pairs": [["Сотрудник", "Иванов И. И."], ["Дата", "2026-09-28T14:05:00+04:00"]]},
            {"type": "bullets", "items": ["Первый пункт", {"text": "Второй", "url": "https://example.gov.ru/2"}]},
            {"type": "warning", "text": "Внимательно проверьте сроки"},
            {"type": "table", "caption": "Список",
             "columns": [{"title": "Тема", "width": 4}, {"title": "Срок", "align": "center"},
                         {"title": "Дней", "align": "right"}, "Кто выдал", "Ссылка"],
             "rows": rows, "totals": ["Итого", None, 300, None, None]},
            {"type": "page_break"},
            {"type": "note", "text": "Конец отчёта"},
        ],
    }


def test_multipage_cyrillic_report(tmp_path):
    out = tmp_path / "отчёт.pdf"
    result = build_pdf.build(long_report(), out)
    assert result == out.resolve()
    assert out.read_bytes()[:5] == b"%PDF-"
    reader = PdfReader(str(out))
    assert len(reader.pages) > 1
    text = text_of(out)
    for word in ("Отчёт о просроченных поручениях", "Сводка", "Поручение №299", "Иванов", "Конец отчёта"):
        assert word in text
    assert "стр. 1 из" in text
    assert f"стр. {len(reader.pages)} из {len(reader.pages)}" in text
    assert "28.09.2026" in text and "2026-09-28" not in text
    assert "28.09.2026 14:05" in text
    assert reader.metadata.title == "Отчёт о просроченных поручениях"
    assert reader.metadata.author == "Аналитический отдел"


def test_header_repeated_on_each_page(tmp_path):
    out = tmp_path / "r.pdf"
    build_pdf.build(long_report(), out)
    pages = PdfReader(str(out)).pages
    table_pages = [p.extract_text() for p in pages if "Поручение №" in p.extract_text()]
    assert len(table_pages) > 2
    assert all("Кто выдал" in page for page in table_pages)


def test_links_become_annotations(tmp_path):
    out = tmp_path / "links.pdf"
    build_pdf.build(long_report(), out)
    uris = link_uris(out)
    assert "https://rx.example.ru/card/0" in uris
    assert "https://example.gov.ru/doc?a=1&b=2" in uris
    assert "https://example.gov.ru/2" in uris


def test_empty_table_renders_no_data(tmp_path):
    out = tmp_path / "empty.pdf"
    spec = {"title": "Пусто", "blocks": [{"type": "table", "columns": ["Тема", "Срок"], "rows": []}]}
    build_pdf.build(spec, out)
    assert "Нет данных" in text_of(out)


def test_none_and_native_values(tmp_path):
    out = tmp_path / "values.pdf"
    spec = {"title": "Значения", "blocks": [{"type": "table", "columns": ["A", "B", "C", "D"],
                                              "rows": [[None, 1234567, 12.5, True]]}]}
    build_pdf.build(spec, out)
    text = text_of(out)
    assert "1 234 567" in text.replace(" ", " ")
    assert "12,5" in text and "Да" in text


def test_very_long_cell_does_not_crash(tmp_path):
    out = tmp_path / "long.pdf"
    huge = ("очень длинный текст ячейки " * 600) + "x" * 3000
    spec = {"title": "Длинная ячейка", "blocks": [
        {"type": "table", "columns": [{"title": "Узкая", "width": 1}, {"title": "Широкая", "width": 5}],
         "rows": [["короткая", huge], [huge, "короткая"]]}]}
    build_pdf.build(spec, out)
    assert len(PdfReader(str(out)).pages) > 1


@pytest.mark.parametrize(
    ("spec", "fragment"),
    [
        ({"title": "T", "blocks": [{"type": "table", "columns": ["A", "B", "C", "D"],
                                    "rows": [["1", "2", "3", "4"], ["1", "2", "3", "4"], ["1", "2", "3"]]}]},
         "blocks[0].rows[2]: ожидалось 4 значения, получено 3"),
        ({"blocks": []}, "отсутствует обязательное поле «title»"),
        ({"title": "T", "blocks": [{"type": "chart"}]}, "blocks[0].type: неизвестный тип блока"),
        ({"title": "T", "blocks": [{"type": "heading", "text": "x", "level": 5}]}, "blocks[0].level"),
        ({"title": "T", "orientation": "wide", "blocks": []}, "orientation"),
        ({"title": "T", "blocks": [{"type": "table", "colums": [], "rows": []}]}, "неизвестное поле «colums»"),
        ({"title": "T", "blocks": [{"type": "paragraph", "text": 5}]}, "blocks[0].text: ожидалась строка"),
    ],
)
def test_invalid_spec_exit_code_2(tmp_path, spec, fragment):
    out = tmp_path / "bad.pdf"
    code, stdout, stderr = run_cli(write_spec(tmp_path, spec), out)
    assert code == 2
    assert "Ошибка спецификации" in stderr
    assert fragment in stderr
    assert not out.exists()


def test_invalid_spec_raises_spec_error():
    with pytest.raises(build_pdf.SpecError) as info:
        build_pdf.validate({"title": "T", "blocks": [{"type": "table", "columns": ["A"], "rows": [["1", "2"]]}]})
    assert "blocks[0].rows[0]: ожидалось 1 значение, получено 2" in str(info.value)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "data:text/html,x", "ftp://x.ru"])
def test_bad_url_in_link_object_rejected(tmp_path, url):
    spec = {"title": "T", "blocks": [{"type": "table", "columns": ["Ссылка"], "rows": [[{"text": "x", "url": url}]]}]}
    code, _, stderr = run_cli(write_spec(tmp_path, spec), tmp_path / "x.pdf")
    assert code == 2
    assert "blocks[0].rows[0][0].url: допустимы только ссылки http:// или https://" in stderr


def test_javascript_markdown_link_rejected():
    with pytest.raises(build_pdf.SpecError) as info:
        build_pdf.validate({"title": "T", "blocks": [{"type": "paragraph", "text": "[жми](javascript:alert(1))"}]})
    assert "blocks[0].text" in str(info.value)


def test_invalid_json_and_missing_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{не json", encoding="utf-8")
    code, _, stderr = run_cli(bad, tmp_path / "x.pdf")
    assert code == 2 and "некорректный JSON" in stderr
    code, _, stderr = run_cli(tmp_path / "нет.json", tmp_path / "x.pdf")
    assert code == 2 and "файл не найден" in stderr


def test_wrong_output_extension(tmp_path):
    code, _, stderr = run_cli(write_spec(tmp_path, {"title": "T", "blocks": []}), tmp_path / "x.docx")
    assert code == 2 and "OUTPUT_PATH" in stderr


def test_row_limit(tmp_path):
    spec = {"title": "T", "blocks": [{"type": "table", "columns": ["A"],
                                      "rows": [["x"]] * (build_pdf.MAX_TABLE_ROWS + 1)}]}
    with pytest.raises(build_pdf.SpecError) as info:
        build_pdf.validate(spec)
    assert "слишком много строк" in str(info.value)


def test_stdin_spec_and_atomic_write(tmp_path):
    out_dir = tmp_path / "вложенная" / "папка"
    out = out_dir / "из_stdin.pdf"
    code, stdout, stderr = run_cli(long_report(), out, stdin=True)
    assert code == 0, stderr
    assert Path(stdout.strip()) == out.resolve()
    assert out.read_bytes()[:5] == b"%PDF-"
    assert [p.name for p in out_dir.iterdir()] == ["из_stdin.pdf"]


def test_failed_render_leaves_no_temp_files(tmp_path, monkeypatch):
    def boom(model, target):
        Path(target).write_bytes(b"%PDF-half")
        raise RuntimeError("сбой")

    monkeypatch.setattr(build_pdf, "_render", boom)
    out = tmp_path / "r.pdf"
    with pytest.raises(RuntimeError):
        build_pdf.build({"title": "T", "blocks": []}, out)
    assert list(tmp_path.iterdir()) == []


def test_existing_file_replaced(tmp_path):
    out = tmp_path / "r.pdf"
    out.write_bytes(b"old")
    build_pdf.build({"title": "Новый", "blocks": []}, out)
    assert out.read_bytes()[:5] == b"%PDF-"
    assert [p.name for p in tmp_path.iterdir()] == ["r.pdf"]


def test_example_spec_builds(tmp_path):
    out = tmp_path / "example.pdf"
    code, stdout, stderr = run_cli(SKILL / "examples" / "example_spec.json", out)
    assert code == 0, stderr
    text = text_of(out)
    assert "Просроченные поручения сотрудника" in text and "Нет данных" in text


def test_format_dates():
    assert build_pdf.format_date_text("2026-09-28") == "28.09.2026"
    assert build_pdf.format_date_text("2026-09-28T00:00:00+04:00") == "28.09.2026"
    assert build_pdf.format_date_text("2026-09-28T09:30:15Z") == "28.09.2026 09:30"
    assert build_pdf.format_date_text("2026-13-45") is None
    assert build_pdf.format_date_text("не дата") is None


def test_bundled_font_found_by_default(monkeypatch):
    monkeypatch.delenv("PDF_FONT_REGULAR", raising=False)
    monkeypatch.delenv("PDF_FONT_BOLD", raising=False)
    regular, bold = build_pdf.find_fonts()
    assert Path(regular) == SKILL / "fonts" / "DejaVuSans.ttf"
    assert Path(bold) == SKILL / "fonts" / "DejaVuSans-Bold.ttf"


def test_missing_font_from_env_exit_code_1(tmp_path):
    import os

    env = {**os.environ, "PDF_FONT_REGULAR": str(tmp_path / "нет.ttf")}
    proc = subprocess.run([sys.executable, str(SCRIPT), str(write_spec(tmp_path, {"title": "T", "blocks": []})),
                           str(tmp_path / "x.pdf")], capture_output=True, env=env, timeout=120)
    assert proc.returncode == 1
    assert "PDF_FONT_REGULAR" in proc.stderr.decode("utf-8")
    assert not (tmp_path / "x.pdf").exists()
    assert [p.name for p in tmp_path.iterdir()] == ["spec.json"]
