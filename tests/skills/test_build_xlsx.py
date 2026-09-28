import importlib.util
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "docs" / "usecases" / "skills" / "excel-report"
SCRIPT = SKILL / "scripts" / "build_xlsx.py"

_spec = importlib.util.spec_from_file_location("build_xlsx", SCRIPT)
build_xlsx = importlib.util.module_from_spec(_spec)
sys.modules["build_xlsx"] = build_xlsx
_spec.loader.exec_module(build_xlsx)


def run_cli(spec, output, stdin=False):
    args = [sys.executable, str(SCRIPT), "-" if stdin else str(spec), str(output)]
    payload = json.dumps(spec, ensure_ascii=False).encode("utf-8") if stdin else None
    proc = subprocess.run(args, input=payload, capture_output=True, timeout=300)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def write_spec(tmp_path, spec):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def typed_spec():
    return {
        "properties": {"title": "Выгрузка", "author": "Аналитик"},
        "sheets": [{
            "name": "Типы",
            "title": "Проверка типов",
            "columns": [
                {"title": "Текст"},
                {"title": "Число", "type": "number"},
                {"title": "Целое", "type": "integer"},
                {"title": "Доля", "type": "percent"},
                {"title": "Дата", "type": "date"},
                {"title": "Дата-время", "type": "datetime"},
                {"title": "Флаг", "type": "bool"},
                {"title": "Ссылка", "type": "url"},
            ],
            "rows": [
                ["=1+1", 12.5, 7, 0.876, "2026-09-28T00:00:00+04:00", "2026-09-28T14:05:00+04:00", True,
                 {"text": "Открыть", "url": "https://rx.example.ru/card/1"}],
                ["+CMD", "1 234,5", "8", "50%", "28.09.2026", "2026-09-28", "нет", "https://rx.example.ru/card/2"],
                ["@SUM(A1)", None, None, None, None, None, None, None],
                ["-2+3", "", "", "", "", "", "", "[Карточка](https://rx.example.ru/card/3)"],
            ],
        }],
    }


def test_cell_types(tmp_path):
    out = tmp_path / "types.xlsx"
    build_xlsx.build(typed_spec(), out)
    ws = load_workbook(out)["Типы"]
    assert ws["A1"].value == "Проверка типов"
    assert [c.value for c in ws[2]][:3] == ["Текст", "Число", "Целое"]
    row = ws[3]
    assert row[1].value == 12.5 and row[1].data_type == "n"
    assert row[2].value == 7 and row[2].number_format == "#,##0"
    assert row[3].value == pytest.approx(0.876) and "%" in row[3].number_format
    assert row[4].value == datetime(2026, 9, 28) and row[4].is_date and row[4].number_format == "DD.MM.YYYY"
    assert row[5].value == datetime(2026, 9, 28, 14, 5) and row[5].number_format == "DD.MM.YYYY HH:MM"
    assert row[6].value is True and row[6].data_type == "b"
    assert row[7].value == "Открыть" and row[7].hyperlink.target == "https://rx.example.ru/card/1"
    row = ws[4]
    assert row[1].value == pytest.approx(1234.5)
    assert row[2].value == 8
    assert row[3].value == pytest.approx(0.5)
    assert row[4].value == datetime(2026, 9, 28)
    assert row[6].value is False
    assert row[7].hyperlink.target == "https://rx.example.ru/card/2"
    assert ws["H6"].value == "Карточка" and ws["H6"].hyperlink.target == "https://rx.example.ru/card/3"
    assert ws["B5"].value is None and ws["B6"].value is None
    wb = load_workbook(out)
    assert wb.properties.title == "Выгрузка" and wb.properties.creator == "Аналитик"


def test_formula_injection_written_as_text(tmp_path):
    out = tmp_path / "inj.xlsx"
    build_xlsx.build(typed_spec(), out)
    ws = load_workbook(out)["Типы"]
    for ref, text in (("A3", "=1+1"), ("A4", "+CMD"), ("A5", "@SUM(A1)"), ("A6", "-2+3")):
        assert ws[ref].value == text
        assert ws[ref].data_type == "s"
        assert ws[ref].quotePrefix is True


def test_formula_injection_in_headers_and_titles(tmp_path):
    out = tmp_path / "inj2.xlsx"
    spec = {"sheets": [{"name": "=x", "title": "=HYPERLINK(\"http://evil\")", "columns": ["=A1"], "rows": [],
                        "totals": {"label": "=2*2", "columns": [0], "func": "count"}}]}
    with pytest.raises(build_xlsx.SpecError):
        build_xlsx.validate(spec)  # подпись некуда поставить: единственная колонка занята итогами
    spec["sheets"][0]["columns"] = ["=A1", "B"]
    build_xlsx.build(spec, out)
    ws = load_workbook(out).worksheets[0]
    assert ws["A1"].data_type == "s" and ws["A2"].data_type == "s" and ws["A2"].value == "=A1"
    assert ws["B4"].value == "=2*2" and ws["B4"].data_type == "s"


def test_sheet_names_sanitized_and_deduplicated(tmp_path):
    out = tmp_path / "names.xlsx"
    long_name = "Очень длинное название листа с поручениями сотрудника"
    spec = {"sheets": [
        {"name": "Поручения: [просрочка]/2026?", "columns": ["A"], "rows": []},
        {"name": long_name, "columns": ["A"], "rows": []},
        {"name": long_name, "columns": ["A"], "rows": []},
        {"name": "поручения  просрочка 2026", "columns": ["A"], "rows": []},
        {"name": "", "columns": ["A"], "rows": []},
        {"name": "'''", "columns": ["A"], "rows": []},
        {"name": "History", "columns": ["A"], "rows": []},
    ]}
    build_xlsx.build(spec, out)
    names = load_workbook(out).sheetnames
    assert names[0] == "Поручения просрочка 2026"
    assert names[1] == long_name[:31]
    assert names[2].endswith(" (2)") and len(names[2]) <= 31
    assert names[3] == "поручения просрочка 2026 (2)"
    assert names[4] == "Лист5" and names[5] == "Лист6"
    assert names[6] == "History_"
    assert len({n.lower() for n in names}) == len(names)
    for name in names:
        assert len(name) <= 31 and not any(ch in name for ch in "[]:*?/\\")


def test_dict_rows_by_key_ignore_extra_fields(tmp_path):
    out = tmp_path / "dict.xlsx"
    spec = {"sheets": [{"name": "Поручения", "columns": [
        {"title": "Тема", "key": "subject"},
        {"title": "Срок", "key": "deadline", "type": "date"},
        {"title": "Дней просрочки", "key": "days_overdue", "type": "integer"},
        {"title": "Ссылка", "key": "url", "type": "url"},
    ], "rows": [
        {"id": 1, "subject": "Подготовить отчёт", "deadline": "2026-09-01T00:00:00+04:00", "days_overdue": 27,
         "url": "https://rx.example.ru/1", "entity_type": "action_item_assignment"},
        {"subject": "Без срока"},
    ]}]}
    build_xlsx.build(spec, out)
    ws = load_workbook(out)["Поручения"]
    assert [c.value for c in ws[2]] == ["Подготовить отчёт", datetime(2026, 9, 1), 27, "https://rx.example.ru/1"]
    assert [c.value for c in ws[3]] == ["Без срока", None, None, None]


def test_totals_formulas_freeze_and_filter(tmp_path):
    out = tmp_path / "totals.xlsx"
    spec = {"sheets": [
        {"name": "Сумма", "title": "Итоги", "columns": [
            "Тема", {"title": "Обращений", "key": "requests", "type": "integer"},
            {"title": "Доля", "key": "share", "type": "percent"}],
         "rows": [["А", 10, 0.25], ["Б", 30, 0.75]],
         "totals": {"label": "Итого", "columns": ["requests", "Доля"], "func": "sum"}},
        {"name": "Среднее", "columns": ["Тема", {"title": "N", "type": "number"}],
         "rows": [["А", 1], ["Б", 2]], "totals": {"columns": [1], "func": "avg"}},
        {"name": "Счёт", "columns": ["Тема", "Автор"], "rows": [["А", "x"], ["Б", None]],
         "totals": {"label": "Всего", "columns": ["Автор"], "func": "count"}},
    ]}
    build_xlsx.build(spec, out)
    wb = load_workbook(out)
    ws = wb["Сумма"]
    assert ws["A5"].value == "Итого"
    assert ws["B5"].value == "=SUM(B3:B4)" and ws["B5"].data_type == "f"
    assert ws["C5"].value == "=SUM(C3:C4)" and "%" in ws["C5"].number_format
    assert ws.freeze_panes == "A3"
    assert ws.auto_filter.ref == "A2:C4"
    assert "A1:C1" in {str(r) for r in ws.merged_cells.ranges}
    ws = wb["Среднее"]
    assert ws["B4"].value == '=IFERROR(AVERAGE(B2:B3),"")'
    assert ws["A4"].value == "Итого"
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref == "A1:B3"
    assert wb["Счёт"]["B4"].value == "=COUNTA(B2:B3)" and wb["Счёт"]["A4"].value == "Всего"


def test_empty_rows_no_data(tmp_path):
    out = tmp_path / "empty.xlsx"
    spec = {"sheets": [{"name": "Пусто", "columns": ["Тема", {"title": "N", "type": "integer"}], "rows": [],
                        "totals": {"columns": [1], "func": "sum"}}]}
    build_xlsx.build(spec, out)
    ws = load_workbook(out)["Пусто"]
    assert ws["A2"].value == "Нет данных"
    assert ws["B3"].value == 0


def test_highlight_conditional_formatting(tmp_path):
    out = tmp_path / "hl.xlsx"
    spec = {"sheets": [{"name": "Л", "columns": [
        "Тема", {"title": "Дней", "key": "d", "type": "integer"}, {"title": "Срок", "type": "date"}],
        "rows": [["А", 40, "2026-09-01"], ["Б", 3, "2026-09-20"]],
        "highlight": [
            {"column": "d", "op": ">=", "value": 30, "color": "red", "row": True},
            {"column": "Срок", "op": "<", "value": "2026-09-10", "color": "#FFEE00"},
            {"column": 0, "op": "==", "value": "Б \"кавычки\""},
        ]}]}
    build_xlsx.build(spec, out)
    ws = load_workbook(out)["Л"]
    rules = {str(cf.sqref): [r.formula[0] for r in cf.rules] for cf in ws.conditional_formatting}
    assert rules["A2:C3"] == ["AND(ISNUMBER($B2),$B2>=30)"]
    assert rules["C2:C3"] == ["AND(ISNUMBER($C2),$C2<DATE(2026,9,10))"]
    assert rules["A2:A3"] == ['AND($A2<>"",$A2="Б ""кавычки""")']


def test_auto_and_fixed_widths(tmp_path):
    out = tmp_path / "w.xlsx"
    spec = {"sheets": [{"name": "Л", "columns": [{"title": "К"}, {"title": "Длинный", "width": 33}, "Средний"],
                        "rows": [["x", "y", "z" * 25], ["a" * 500, "b", "c"]]}]}
    build_xlsx.build(spec, out)
    ws = load_workbook(out)["Л"]
    assert ws.column_dimensions["A"].width == 60
    assert ws.column_dimensions["B"].width == 33
    assert ws.column_dimensions["C"].width == 27
    assert ws["A3"].alignment.wrap_text is True


@pytest.mark.parametrize(
    ("spec", "fragment"),
    [
        ({"sheets": [{"name": "Л", "columns": ["A", "B", "C", "D"], "rows": [[1, 2, 3, 4], [1, 2, 3]]}]},
         "sheets[0].rows[1]: ожидалось 4 значения, получено 3"),
        ({"sheets": []}, "sheets: ожидался непустой список листов"),
        ({}, "отсутствует обязательное поле «sheets»"),
        ({"sheets": [{"name": "Л", "columns": [{"title": "N", "type": "money"}], "rows": []}]},
         "sheets[0].columns[0].type: неизвестный тип 'money'"),
        ({"sheets": [{"name": "Л", "columns": [{"title": "N", "type": "integer"}], "rows": [["пять"]]}]},
         "sheets[0].rows[0][0]: ожидалось число для колонки типа integer"),
        ({"sheets": [{"name": "Л", "columns": [{"title": "N", "type": "integer"}], "rows": [[1.5]]}]},
         "sheets[0].rows[0][0]: ожидалось целое число"),
        ({"sheets": [{"name": "Л", "columns": [{"title": "Д", "type": "date"}], "rows": [["вчера"]]}]},
         "sheets[0].rows[0][0]: ожидалась дата"),
        ({"sheets": [{"name": "Л", "columns": [{"title": "U", "type": "url"}], "rows": [["javascript:alert(1)"]]}]},
         "sheets[0].rows[0][0]: допустимы только ссылки http:// или https://"),
        ({"sheets": [{"name": "Л", "columns": ["A"], "rows": [[{"text": "x", "url": "javascript:alert(1)"}]]}]},
         "sheets[0].rows[0][0].url: допустимы только ссылки"),
        ({"sheets": [{"name": "Л", "columns": ["A"], "rows": [["[x](javascript:alert(1))"]]}]},
         "sheets[0].rows[0][0]: допустимы только ссылки"),
        ({"sheets": [{"name": "Л", "columns": ["A"], "rows": [{"a": 1}]}]}, "нет key"),
        ({"sheets": [{"name": "Л", "columns": ["A", "B"], "rows": [], "totals": {"columns": ["B"], "func": "sum"}}]},
         "sum применим только к числовым колонкам"),
        ({"sheets": [{"name": "Л", "columns": ["A"], "rows": [], "totals": {"columns": ["Z"], "func": "sum"}}]},
         "sheets[0].totals.columns[0]: колонка 'Z' не найдена"),
        ({"sheets": [{"name": "Л", "columns": ["A"], "rows": [], "totals": {"columns": [0], "func": "max"}}]},
         "sheets[0].totals.func"),
        ({"sheets": [{"name": "Л", "columns": ["A"], "rows": [],
                      "highlight": [{"column": 0, "op": ">", "value": "x"}]}]},
         "для текстовой колонки допустимы только операции == и !="),
        ({"sheets": [{"name": "Л", "columns": [{"title": "N", "type": "number"}], "rows": [],
                      "highlight": [{"column": 0, "op": "~", "value": 1}]}]}, "sheets[0].highlight[0].op"),
        ({"sheets": [{"name": "Л", "columns": ["A"], "rows": [], "colour": 1}]}, "неизвестное поле «colour»"),
    ],
)
def test_invalid_spec_exit_code_2(tmp_path, spec, fragment):
    out = tmp_path / "bad.xlsx"
    code, _, stderr = run_cli(write_spec(tmp_path, spec), out)
    assert code == 2, stderr
    assert "Ошибка спецификации" in stderr
    assert fragment in stderr
    assert not out.exists()


def test_row_limit_error():
    rows = [[1]] * (build_xlsx.MAX_ROWS_PER_SHEET + 1)
    with pytest.raises(build_xlsx.SpecError) as info:
        build_xlsx.validate({"sheets": [{"name": "Л", "columns": ["A"], "rows": rows}]})
    assert f"sheets[0].rows: слишком много строк ({build_xlsx.MAX_ROWS_PER_SHEET + 1})" in str(info.value)


def test_sheet_limit_error():
    sheets = [{"name": f"Л{i}", "columns": ["A"], "rows": []} for i in range(build_xlsx.MAX_SHEETS + 1)]
    with pytest.raises(build_xlsx.SpecError) as info:
        build_xlsx.validate({"sheets": sheets})
    assert "слишком много листов" in str(info.value)


def test_cell_length_limit_error():
    with pytest.raises(build_xlsx.SpecError) as info:
        build_xlsx.validate({"sheets": [{"name": "Л", "columns": ["A"], "rows": [["я" * 40000]]}]})
    assert "sheets[0].rows[0][0]: текст длиннее" in str(info.value)


def test_control_chars_removed(tmp_path):
    out = tmp_path / "ctrl.xlsx"
    build_xlsx.build({"sheets": [{"name": "Л", "columns": ["A"], "rows": [["до\x00\x07после\nстрока"]]}]}, out)
    assert load_workbook(out)["Л"]["A2"].value == "допосле\nстрока"


def test_native_python_dates(tmp_path):
    out = tmp_path / "native.xlsx"
    spec = {"sheets": [{"name": "Л", "columns": [{"title": "Д", "type": "date"}, "Текст"],
                        "rows": [[date(2026, 1, 2), "2026-01-02"]]}]}
    build_xlsx.build(spec, out)
    ws = load_workbook(out)["Л"]
    assert ws["A2"].value == datetime(2026, 1, 2)
    assert ws["B2"].value == "02.01.2026"


def test_stdin_and_atomic_write(tmp_path):
    out_dir = tmp_path / "новая" / "папка"
    out = out_dir / "выгрузка.xlsx"
    code, stdout, stderr = run_cli(typed_spec(), out, stdin=True)
    assert code == 0, stderr
    assert Path(stdout.strip()) == out.resolve()
    load_workbook(out)
    assert [p.name for p in out_dir.iterdir()] == ["выгрузка.xlsx"]


def test_failed_render_leaves_no_temp_files(tmp_path, monkeypatch):
    def boom(model, target):
        Path(target).write_bytes(b"PK half")
        raise RuntimeError("сбой")

    monkeypatch.setattr(build_xlsx, "_render", boom)
    out = tmp_path / "r.xlsx"
    out.write_bytes(b"old")
    with pytest.raises(RuntimeError):
        build_xlsx.build(typed_spec(), out)
    assert [p.name for p in tmp_path.iterdir()] == ["r.xlsx"]
    assert out.read_bytes() == b"old"


def test_unexpected_error_exit_code_1(tmp_path):
    blocker = tmp_path / "файл"
    blocker.write_text("x", encoding="utf-8")
    code, _, stderr = run_cli(write_spec(tmp_path, typed_spec()), blocker / "out.xlsx")
    assert code == 1
    assert "Непредвиденная ошибка" in stderr


def test_wrong_output_extension(tmp_path):
    code, _, stderr = run_cli(write_spec(tmp_path, typed_spec()), tmp_path / "out.csv")
    assert code == 2 and "OUTPUT_PATH" in stderr


def test_example_spec_builds(tmp_path):
    out = tmp_path / "example.xlsx"
    code, _, stderr = run_cli(SKILL / "examples" / "example_spec.json", out)
    assert code == 0, stderr
    wb = load_workbook(out)
    assert wb.sheetnames == ["Просроченные поручения", "Обращения по тематикам"]
    ws = wb["Просроченные поручения"]
    assert ws["B3"].value == datetime(2026, 8, 18)
    assert ws["E3"].hyperlink.target.startswith("https://")
