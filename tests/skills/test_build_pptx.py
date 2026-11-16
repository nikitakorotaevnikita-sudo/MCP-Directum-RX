import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Emu, Inches

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "docs" / "usecases" / "skills" / "pptx-creator"
SCRIPT = SKILL / "scripts" / "build_pptx.py"
EXAMPLE = SKILL / "examples" / "example_spec.json"

_spec = importlib.util.spec_from_file_location("build_pptx", SCRIPT)
build_pptx = importlib.util.module_from_spec(_spec)
sys.modules["build_pptx"] = build_pptx
_spec.loader.exec_module(build_pptx)


def base_spec():
    return {
        "title": "Исполнительская дисциплина",
        "subtitle": "Итоги октября 2026",
        "author": "Отдел контроля",
        "date": "2026-11-16",
        "slides": [
            {"title": "Содержание", "bullets": ["Ситуация", "Данные", "Что дальше"]},
            {"title": "Суть и проблема", "bullets": [
                "Просрочки растут", {"text": "В основном — по обращениям", "level": 1}],
                "metric": {"value": "45", "label": "поручений просрочено"}},
            {"title": "Ключевые данные", "bullets": ["Худшие подразделения"],
             "table": {"columns": ["Подразделение", "Просрочено"],
                       "rows": [["Отдел ЖКХ", 12], ["Отдел дорог", 9]]}},
            {"title": "Что дальше", "bullets": [
                "Еженедельный контроль", {"text": "Карточка", "url": "https://rx.example.ru/card/1"}],
             "notes": "Сказать про сроки"},
        ],
    }


def all_text(slide):
    parts = []
    for shape in slide.shapes:
        if shape.has_text_frame:
            parts.append(shape.text_frame.text)
        if shape.has_table:
            parts += [cell.text for row in shape.table.rows for cell in row.cells]
    return "\n".join(parts)


def run_cli(spec, output, stdin=False, tmp_path=None):
    if stdin:
        args = [sys.executable, str(SCRIPT), "-", str(output)]
        payload = json.dumps(spec, ensure_ascii=False).encode("utf-8")
    else:
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        args, payload = [sys.executable, str(SCRIPT), str(spec_path), str(output)], None
    proc = subprocess.run(args, input=payload, capture_output=True, timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def test_builds_exactly_five_widescreen_slides(tmp_path):
    out = build_pptx.build(base_spec(), tmp_path / "deck.pptx")
    prs = Presentation(str(out))
    assert len(prs.slides) == 5
    assert prs.slide_width == Inches(13.333) and prs.slide_height == Inches(7.5)


def test_title_slide_has_title_subtitle_author_and_russian_date(tmp_path):
    prs = Presentation(str(build_pptx.build(base_spec(), tmp_path / "deck.pptx")))
    text = all_text(prs.slides[0])
    for part in ["Исполнительская дисциплина", "Итоги октября 2026", "Отдел контроля", "16.11.2026"]:
        assert part in text


def test_content_slides_render_bullets_table_metric_and_link(tmp_path):
    prs = Presentation(str(build_pptx.build(base_spec(), tmp_path / "deck.pptx")))
    assert "Просрочки растут" in all_text(prs.slides[2]) and "45" in all_text(prs.slides[2])
    tables = [s for s in prs.slides[3].shapes if s.has_table]
    assert len(tables) == 1 and tables[0].table.cell(1, 0).text == "Отдел ЖКХ"
    runs = [r for s in prs.slides[4].shapes if s.has_text_frame for p in s.text_frame.paragraphs for r in p.runs]
    assert any(r.hyperlink.address == "https://rx.example.ru/card/1" for r in runs)


def test_sub_bullet_has_level_one(tmp_path):
    prs = Presentation(str(build_pptx.build(base_spec(), tmp_path / "deck.pptx")))
    levels = {p.text.lstrip("■–  "): p.level
              for s in prs.slides[2].shapes if s.has_text_frame for p in s.text_frame.paragraphs}
    assert levels["Просрочки растут"] == 0 and levels["В основном — по обращениям"] == 1


def test_speaker_notes_and_slide_numbers(tmp_path):
    prs = Presentation(str(build_pptx.build(base_spec(), tmp_path / "deck.pptx")))
    assert prs.slides[4].notes_slide.notes_text_frame.text == "Сказать про сроки"
    assert "5 / 5" in all_text(prs.slides[4])


def test_pending_slide_is_marked(tmp_path):
    spec = base_spec()
    spec["slides"][2]["pending"] = True
    prs = Presentation(str(build_pptx.build(spec, tmp_path / "deck.pptx")))
    assert "данные уточняются" in all_text(prs.slides[3])


def test_iso_dates_in_table_cells_become_russian(tmp_path):
    spec = base_spec()
    spec["slides"][2]["table"]["rows"][0][1] = "2026-10-31"
    prs = Presentation(str(build_pptx.build(spec, tmp_path / "deck.pptx")))
    table = next(s for s in prs.slides[3].shapes if s.has_table).table
    assert table.cell(1, 1).text == "31.10.2026"


@pytest.mark.parametrize("count", [3, 5])
def test_requires_exactly_four_content_slides(tmp_path, count):
    spec = base_spec()
    spec["slides"] = (spec["slides"] * 2)[:count]
    with pytest.raises(build_pptx.SpecError, match="ровно 4"):
        build_pptx.build(spec, tmp_path / "deck.pptx")


def test_errors_are_collected_with_paths(tmp_path):
    spec = base_spec()
    spec["slides"][0]["bullets"] = [f"Пункт {i}" for i in range(6)]
    spec["slides"][2]["table"]["rows"].append(["только одно значение"])
    spec["slides"][3]["bullets"][1]["url"] = "javascript:alert(1)"
    with pytest.raises(build_pptx.SpecError) as info:
        build_pptx.build(spec, tmp_path / "deck.pptx")
    text = str(info.value)
    assert "slides[0].bullets" in text and "не больше 5" in text
    assert "slides[2].table.rows[2]" in text
    assert "slides[3].bullets[1].url" in text and "http" in text


def test_missing_title_and_unknown_field(tmp_path):
    spec = base_spec()
    del spec["title"]
    spec["slides"][1]["colour"] = "red"
    with pytest.raises(build_pptx.SpecError) as info:
        build_pptx.build(spec, tmp_path / "deck.pptx")
    assert "title" in str(info.value) and "colour" in str(info.value)


def test_output_must_be_pptx(tmp_path):
    with pytest.raises(build_pptx.SpecError, match=r"\.pptx"):
        build_pptx.build(base_spec(), tmp_path / "deck.pdf")


def test_atomic_write_leaves_no_temp_files(tmp_path):
    build_pptx.build(base_spec(), tmp_path / "nested" / "deck.pptx")
    assert sorted(p.name for p in (tmp_path / "nested").iterdir()) == ["deck.pptx"]


def test_example_spec_builds(tmp_path):
    spec = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    prs = Presentation(str(build_pptx.build(spec, tmp_path / "example.pptx")))
    assert len(prs.slides) == 5


def test_cli_success_prints_path(tmp_path):
    code, out, err = run_cli(base_spec(), tmp_path / "deck.pptx", tmp_path=tmp_path)
    assert code == 0, err
    assert Path(out.strip()) == (tmp_path / "deck.pptx").resolve()


def test_cli_stdin_and_spec_error_exit_code(tmp_path):
    code, _, err = run_cli(base_spec(), tmp_path / "deck.pptx", stdin=True)
    assert code == 0, err
    bad = copy.deepcopy(base_spec())
    bad["slides"] = bad["slides"][:2]
    code, _, err = run_cli(bad, tmp_path / "bad.pptx", stdin=True)
    assert code == 2 and "Ошибка спецификации" in err and "ровно 4" in err
    assert not (tmp_path / "bad.pptx").exists()


def test_shapes_stay_inside_the_slide(tmp_path):
    prs = Presentation(str(build_pptx.build(base_spec(), tmp_path / "deck.pptx")))
    for slide in prs.slides:
        for shape in slide.shapes:
            assert shape.left >= 0 and shape.top >= 0
            assert shape.left + shape.width <= prs.slide_width + Emu(1)
            assert shape.top + shape.height <= prs.slide_height + Emu(1)
