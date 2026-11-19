import importlib.util
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "docs" / "usecases" / "skills" / "xlsx-read"
SCRIPT = SKILL / "scripts" / "read_tables.py"
OCT = SKILL / "examples" / "report_oct.xlsx"
NOV = SKILL / "examples" / "report_nov.xlsx"

_spec = importlib.util.spec_from_file_location("read_tables", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["read_tables"] = mod
_spec.loader.exec_module(mod)


def run(*args):
    proc = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def run_json(*args):
    code, out, err = run(*args, "--format", "json")
    assert code == 0, err
    return json.loads(out)


# --- значения ---


@pytest.mark.parametrize("text, expected", [
    ("1 234,50", 1234.5), ("1 234,5", 1234.5), ("-12.5", -12.5), ("−7", -7), ("12,5%", 0.125),
    ("300 ₽", 300), ("42", 42), ("0,5", 0.5),
])
def test_parse_number(text, expected):
    assert mod.parse_number(text)[0] == expected


@pytest.mark.parametrize("text", ["007", "7701234567", "0004.0015", "+7 912 345 67 89", "1 2 3", "abc", ""])
def test_codes_are_not_numbers(text):
    assert mod.parse_number(text) is None


def test_parse_date():
    assert mod.parse_date("01.11.2026") == (datetime(2026, 11, 1), False)
    assert mod.parse_date("1.11.26 10:30") == (datetime(2026, 11, 1, 10, 30), True)
    assert mod.parse_date("2026-10-14T13:48:22+04:00") == (datetime(2026, 10, 14, 13, 48, 22), True)
    assert mod.parse_date("31.02.2026") is None


def test_classify_tolerates_few_strays():
    assert mod.classify([95000, 52000.5, "1 234,50", "н/д", 15000]) == "number"
    assert mod.classify([1, 2, "3"]) == "integer"
    assert mod.classify(["a", "b", 3]) == "text"
    assert mod.classify(["7701234567", "7702345678"]) == "text"
    assert mod.classify([None]) == "empty"


# --- разбор «грязного» листа ---


def test_inspect_finds_table_in_messy_report():
    data = run_json("inspect", OCT)
    sheets = {s["sheet"]: s for s in data["files"][0]["sheets"]}
    assert sheets["Служебный"]["state"] == "hidden"
    t = sheets["Поручения"]["table"]
    assert t["header_rows"] == [4, 5] and t["rows"] == 6 and t["data_range"] == "A8:G15"
    cols = {c["name"]: c["type"] for c in t["columns"]}
    assert cols == {"Раздел": "text", "№": "integer", "Поручение": "text", "Исполнитель": "text", "Срок": "date",
                    "Сумма, руб. / План": "number", "Сумма, руб. / Факт": "number", "Статус": "text"}
    assert [x["label"] for x in t["totals_rows"]] == ["Итого по отделу", "Итого по отделу", "Всего"]
    assert t["sections"] == ["Отдел дорожного хозяйства", "Отдел ЖКХ"]
    assert t["footer_notes"] == ["Исполнитель: Морозова Е. В., тел. 12-34"]
    assert sheets["Поручения"]["hidden_rows"] == 1
    warnings = " ".join(sheets["Поручения"]["warnings"])
    assert "нумерацией" in warnings and "«н/д»" in warnings


def test_read_converts_text_numbers_and_dates():
    data = run_json("read", OCT)
    row = next(r for r in data["rows"] if r["№"] == 4)
    assert row["Срок"] == "2026-10-15" and row["Сумма, руб. / План"] == 1234.5 and row["Раздел"] == "Отдел ЖКХ"
    assert next(r for r in data["rows"] if r["№"] == 5)["Сумма, руб. / Факт"] == "н/д"
    assert any("взят лист «Поручения»" in w for w in data["warnings"])


def test_keep_totals_and_skip_hidden():
    assert run_json("read", OCT, "--keep-totals")["rows_total"] == 9
    assert run_json("read", OCT, "--skip-hidden")["rows_total"] == 5


def test_profile_checks_grand_total():
    data = run_json("profile", OCT)["tables"][0]
    assert {c["column"]: c["match"] for c in data["totals_check"]} == {
        "Сумма, руб. / План": True, "Сумма, руб. / Факт": True}
    fact = next(c for c in data["columns"] if c["name"] == "Сумма, руб. / Факт")
    assert fact["sum"] == 163235 and fact["nonconforming"] == 1 and fact["nonconforming_examples"] == ["н/д"]


def test_sheet_by_name_and_index():
    assert run_json("read", f"{OCT}::справочник")["columns"][2] == {"name": "ИНН организации", "type": "text"}
    assert run_json("read", f"{OCT}::2")["rows_total"] == 4
    code, _, err = run("read", f"{OCT}::Нет такого")
    assert code == 2 and "Листы: Поручения, Справочник, Служебный" in err


# --- отбор и свод ---


def test_query_where_and_select():
    data = run_json("query", OCT, "--where", "Статус != Исполнено", "--where", "Срок < 01.11.2026",
                    "--select", "№,Статус")
    assert data["rows"] == [{"№": 4, "Статус": "Просрочено"}, {"№": 5, "Статус": "В работе"}]
    assert data["rows_matched"] == 2


@pytest.mark.parametrize("where, numbers", [
    ("Поручение ~ ремонт", [1]), ("Статус in Просрочено|В работе", [3, 4, 5]), ("Факт is empty", [3]),
    ("План >= 50000", [1, 2, 3]), ("Исполнитель = иванова а. а.", [4, 6]), ("Поручение !~ ремонт", [2, 3, 4, 5, 6]),
])
def test_where_operators(where, numbers):
    assert [r["№"] for r in run_json("query", OCT, "--where", where)["rows"]] == numbers


def test_group_by_with_share_and_sort():
    data = run_json("query", OCT, "--group-by", "Исполнитель", "--agg", "count,sum:План", "--share",
                    "--sort", "Сумма: Сумма, руб. / План:desc")
    rows = data["rows"]
    assert rows[0] == {"Исполнитель": "Петров П. П.", "Количество": 2, "Сумма: Сумма, руб. / План": 180000,
                       "Доля, % (Количество)": 33.3}
    assert [r["Исполнитель"] for r in rows] == ["Петров П. П.", "Сидоров С. С.", "Кузнецов К. К.", "Иванова А. А."]


def test_agg_without_group():
    rows = run_json("query", OCT, "--agg", "sum:Факт,avg:План,max:Срок,nunique:Исполнитель")["rows"]
    assert rows == [{"Сумма: Сумма, руб. / Факт": 163235, "Среднее: Сумма, руб. / План": 44372.4166666667,
                     "Максимум: Срок": "2026-11-05", "Уникальных: Исполнитель": 4}]


@pytest.mark.parametrize("args, message", [
    (("--where", "План >> 5"), "не число"),
    (("--where", "Сумма > 5"), "неоднозначна"),
    (("--where", "Нет колонки = 1"), "не найдена"),
    (("--where", "План > много"), "не число"),
    (("--where", "Срок < вчера"), "не дата"),
    (("--agg", "sum"), "укажите колонку"),
    (("--agg", "total:План"), "функции"),
])
def test_bad_queries(args, message):
    code, _, err = run("query", OCT, *args)
    assert code == 2 and message in err


# --- несколько таблиц ---


def test_combine_two_files_aligns_columns():
    data = run_json("combine", OCT, NOV)
    assert data["rows_total"] == 12
    assert [c["name"] for c in data["columns"]][:3] == ["Файл", "Лист", "Раздел"]
    assert {r["Файл"] for r in data["rows"]} == {"report_oct.xlsx", "report_nov.xlsx"}
    assert any("report_nov.xlsx :: Поручения: нет колонок «Раздел»" in w for w in data["warnings"])


def test_query_over_several_files():
    data = run_json("query", OCT, NOV, "--group-by", "Файл", "--agg", "sum:План")
    assert {r["Файл"]: r["Сумма: Сумма, руб. / План"] for r in data["rows"]} == {
        "report_oct.xlsx": 266234.5, "report_nov.xlsx": 486234.5}


def test_diff_by_key():
    data = run_json("diff", OCT, NOV, "--key", "№")
    assert (data["added_count"], data["removed_count"], data["changed_count"], data["unchanged_count"]) == (1, 1, 3, 2)
    assert data["added"][0]["Поручение"] == "Ремонт моста" and data["removed"][0]["№"] == 3
    changes = {(c["key"]["№"], ch["column"]): (ch["a"], ch["b"]) for c in data["changed"] for ch in c["changes"]}
    assert changes[(4, "Статус")] == ("Просрочено", "Исполнено")
    assert changes[(2, "Сумма, руб. / Факт")] == (52000.5, 50000)
    assert data["columns_only_a"] == ["Раздел"]


def test_diff_requires_two_tables():
    code, _, err = run("diff", OCT, "--key", "№")
    assert code == 2 and "ровно две" in err


def test_join_adds_columns():
    data = run_json("join", OCT, f"{OCT}::Справочник", "--key", "Исполнитель", "--columns", "Подразделение")
    assert data["matched"] == 6 and data["rows"][0]["Подразделение"] == "Отдел дорожного хозяйства"


# --- форматы и вход ---


def test_xlsx_spec_for_excel_report(tmp_path):
    out = tmp_path / "spec.json"
    code, stdout, err = run("query", OCT, "--group-by", "Раздел", "--agg", "sum:План", "--format", "xlsx-spec",
                            "--out", out)
    assert code == 0 and stdout.strip() == str(out), err
    spec = json.loads(out.read_text(encoding="utf-8"))
    sheet = spec["sheets"][0]
    assert sheet["columns"][1] == {"title": "Сумма: Сумма, руб. / План", "key": "c2", "type": "number"}
    assert sheet["rows"][0] == {"c1": "Отдел дорожного хозяйства", "c2": 230000}


def test_csv_cp1251_semicolon(tmp_path):
    path = tmp_path / "export.csv"
    path.write_bytes("Отчёт\n\nФИО;Сумма;Дата\nИванов;1 000,50;01.10.2026\nПетров;200;02.10.2026\n"
                     "Итого;1 200,50;\n".encode("cp1251"))
    data = run_json("read", path)
    assert data["rows"] == [{"ФИО": "Иванов", "Сумма": 1000.5, "Дата": "2026-10-01"},
                            {"ФИО": "Петров", "Сумма": 200, "Дата": "2026-10-02"}]
    code, out, _ = run("read", path, "--format", "csv")
    assert out.splitlines()[1] == "Иванов;1000,5;2026-10-01"


def test_no_header_and_header_row(tmp_path):
    path = tmp_path / "plain.xlsx"
    wb = openpyxl.Workbook()
    for row in ([1, 2.5], [3, 4.5]):
        wb.active.append(row)
    wb.save(path)
    data = run_json("read", path)
    assert [c["name"] for c in data["columns"]] == ["Колонка A", "Колонка B"]
    assert any("Строка заголовков не найдена" in w for w in data["warnings"])
    assert run_json("read", path, "--header-row", "1")["columns"][1]["name"] == "2.5"


def test_inflated_sheet_and_formulas_without_values(tmp_path):
    path = tmp_path / "big.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Имя", "Значение"])
    ws.append(["а", 1])
    ws["B3"] = "=SUM(B2:B2)"  # openpyxl не считает формулы — значения в файле нет
    ws.row_dimensions[500000].height = 20
    wb.save(path)
    sheet = run_json("inspect", path)["files"][0]["sheets"][0]
    assert sheet["used_range"] == "A1:B2" and sheet["formulas_without_values"] == 1
    assert any("формул без сохранённых значений" in w for w in sheet["warnings"])


@pytest.mark.parametrize("name, content, message", [
    ("old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "старый формат .xls"),
    ("bad.xlsx", b"not a zip", "не похоже на файл Excel"),
    ("table.ods", b"PK\x03\x04", "не поддерживается"),
])
def test_bad_files(tmp_path, name, content, message):
    path = tmp_path / name
    path.write_bytes(content)
    code, _, err = run("inspect", path)
    assert code == 2 and message in err


def test_missing_file():
    code, _, err = run("read", "нет-такого.xlsx")
    assert code == 2 and "Файл не найден" in err


def test_md_formats_numbers_and_dates():
    code, out, _ = run("read", OCT, "--format", "md", "--limit", "2")
    assert code == 0 and "| 100 000 |" in out and "10.10.2026" in out and "Показано 2 из 6" in out


def test_limit_one():
    code, out, _ = run("read", OCT, "--format", "json", "--limit", "1")
    assert code == 0 and json.loads(out)["returned"] == 1
