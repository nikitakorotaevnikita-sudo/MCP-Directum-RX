"""Пересобрать примеры навыка xlsx-read: python examples/make_example.py

report_oct.xlsx — «грязная» выгрузка: заголовок отчёта, шапка в две строки с объединёнными ячейками, строка
нумерации колонок, строки-разделы, промежуточные итоги и общий «Всего», подпись под таблицей, числа и дата
текстом, «н/д» в числовой колонке, скрытая строка, второй лист-справочник и скрытый служебный лист.
report_nov.xlsx — та же таблица месяцем позже (простая шапка) — для сверки (diff) по колонке «№».
"""

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

HERE = Path(__file__).resolve().parent
STAMP = datetime(2026, 11, 1, 9, 0, 0)
HEAD = ["№", "Поручение", "Исполнитель", "Срок", "Сумма, руб.", None, "Статус"]


def fixed(wb):
    wb.properties.creator = "xlsx-read example"
    wb.properties.created = STAMP
    wb.properties.modified = STAMP


def october(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Поручения"
    ws["A1"] = "Отчёт об исполнении поручений за октябрь 2026"
    ws.merge_cells("A1:G1")
    ws["A2"] = "по состоянию на 01.11.2026"
    ws.merge_cells("A2:G2")
    for col, title in enumerate(HEAD, start=1):
        if title:
            ws.cell(4, col, title)
    ws["E5"], ws["F5"] = "План", "Факт"
    for rng in ("A4:A5", "B4:B5", "C4:C5", "D4:D5", "E4:F4", "G4:G5"):
        ws.merge_cells(rng)
    for col in range(1, 8):
        ws.cell(6, col, col)
    rows = [
        ("section", "Отдел дорожного хозяйства"),
        (1, "Ремонт ул. Ленина", "Петров П. П.", datetime(2026, 10, 10), 100000, 95000, "Исполнено"),
        (2, "Разметка дорог", "Сидоров С. С.", datetime(2026, 10, 20), 50000, 52000.5, "Исполнено"),
        (3, "Освещение парка", "Петров П. П.", datetime(2026, 11, 5), 80000, None, "В работе"),
        ("Итого по отделу", None, None, None, 230000, 147000.5, None),
        ("section", "Отдел ЖКХ"),
        (4, "Замена труб", "Иванова А. А.", "15.10.2026", "1 234,50", "1 234,50", "Просрочено"),
        (5, "Вывоз снега", "Кузнецов К. К.", datetime(2026, 10, 25), 20000, "н/д", "В работе"),
        (6, "Озеленение двора", "Иванова А. А.", datetime(2026, 10, 30), 15000, 15000, "Исполнено"),
        ("Итого по отделу", None, None, None, 36234.5, 16234.5, None),
        ("Всего", None, None, None, 266234.5, 163235, None),
    ]
    r = 7
    for row in rows:
        if row[0] == "section":
            ws.cell(r, 1, row[1])
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=7)
        else:
            for col, value in enumerate(row, start=1):
                if value is not None:
                    cell = ws.cell(r, col, value)
                    if isinstance(value, datetime):
                        cell.number_format = "DD.MM.YYYY"
        r += 1
    ws.row_dimensions[13].hidden = True  # «Вывоз снега»
    ws.cell(r + 1, 1, "Исполнитель: Морозова Е. В., тел. 12-34")

    ref = wb.create_sheet("Справочник")
    ref.append(["Исполнитель", "Подразделение", "ИНН организации"])
    ref.append(["Петров П. П.", "Отдел дорожного хозяйства", "7701234567"])
    ref.append(["Сидоров С. С.", "Отдел дорожного хозяйства", "7701234567"])
    ref.append(["Иванова А. А.", "Отдел ЖКХ", "7702345678"])
    ref.append(["Кузнецов К. К.", "Отдел ЖКХ", "7702345678"])

    hidden = wb.create_sheet("Служебный")
    hidden.append(["ключ", "значение"])
    hidden.append(["версия", 3])
    hidden.sheet_state = "hidden"
    fixed(wb)
    wb.save(path)


def november(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Поручения"
    ws.append(["№", "Поручение", "Исполнитель", "Срок", "Сумма, руб. / План", "Сумма, руб. / Факт", "Статус"])
    for row in [
        (1, "Ремонт ул. Ленина", "Петров П. П.", datetime(2026, 10, 10), 100000, 95000, "Исполнено"),
        (2, "Разметка дорог", "Сидоров С. С.", datetime(2026, 10, 20), 50000, 50000, "Исполнено"),
        (4, "Замена труб", "Иванова А. А.", datetime(2026, 10, 15), 1234.5, 1234.5, "Исполнено"),
        (5, "Вывоз снега", "Кузнецов К. К.", datetime(2026, 10, 25), 20000, None, "В работе"),
        (6, "Озеленение двора", "Иванова А. А.", datetime(2026, 10, 30), 15000, 15000, "Исполнено"),
        (7, "Ремонт моста", "Петров П. П.", datetime(2026, 12, 1), 300000, None, "В работе"),
    ]:
        ws.append(row)
        ws.cell(ws.max_row, 4).number_format = "DD.MM.YYYY"
    fixed(wb)
    wb.save(path)


if __name__ == "__main__":
    october(HERE / "report_oct.xlsx")
    november(HERE / "report_nov.xlsx")
    print("ok")
