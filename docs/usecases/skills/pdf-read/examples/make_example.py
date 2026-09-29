"""Пересобрать examples/example.pdf (нужен reportlab): 2 страницы текста и страница-«скан» без текстового слоя."""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent
FONT = HERE.parents[1] / "pdf-report" / "fonts" / "DejaVuSans.ttf"


def build(path: Path) -> Path:
    pdfmetrics.registerFont(TTFont("DejaVu", str(FONT)))
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    c.setTitle("Постановление о благоустройстве")
    c.setAuthor("Отдел по работе с ПА")
    pages = [
        ["ПОСТАНОВЛЕНИЕ", "от 15.09.2026 № 245", "", "Об утверждении порядка благо-",
         "устройства дворовых территорий", "", "1. Утвердить прилагаемый порядок."],
        ["2. Контроль за исполнением возложить", "на заместителя главы администрации.",
         "3. Постановление вступает в силу со дня опубликования."],
    ]
    for lines in pages:
        c.setFont("DejaVu", 12)
        y = 800
        for line in lines:
            c.drawString(72, y, line)
            y -= 18
        c.showPage()
    c.rect(72, 400, 300, 200, fill=1)  # «скан»: только графика, текста нет
    c.showPage()
    c.save()
    return path


if __name__ == "__main__":
    print(build(HERE / "example.pdf"))
