"""Generate sample PDFs for tests (reportlab is a dev-only dependency)."""
import os, io
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'samples')

def font():
    for p in ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
        if os.path.exists(p):
            pdfmetrics.registerFont(TTFont('DejaVu', p))
            b = p.replace('DejaVuSans.ttf', 'DejaVuSans-Bold.ttf')
            if os.path.exists(b): pdfmetrics.registerFont(TTFont('DejaVu-Bold', b))
            return 'DejaVu'
    return 'Helvetica'

def make_report(path):
    f = font(); fb = f + '-Bold' if f == 'DejaVu' else 'Helvetica-Bold'
    c = canvas.Canvas(path, pagesize=A4); W, H = A4
    c.setFont(fb, 16); c.drawString(60, H-70, 'Отчёт компании: выручка по сегментам')
    c.setFont(f, 10)
    y = H-100
    for line in ['Выручка выросла на 12% год к году благодаря облачному сегменту.',
                 'Операционная маржа составила 31,4%, свободный денежный поток — 2,1 млрд.']:
        c.drawString(60, y, line); y -= 14
    # lattice table
    cols = [60, 220, 320, 420, 520]; rows = ['Сегмент|2024|2025|Изм., %', 'Облако|1 200|1 450|20,8', 'Реклама|900|960|6,7', 'Прочее|300|290|-3,3', 'Итого|2 400|2 700|12,5']
    top = y - 20; rh = 20
    c.setFillColorRGB(0.85, 0.9, 1.0); c.rect(cols[0], top-rh, cols[-1]-cols[0], rh, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0); c.setLineWidth(0.8)
    for i in range(len(rows)+1): c.line(cols[0], top-i*rh, cols[-1], top-i*rh)
    for x in cols: c.line(x, top, x, top-len(rows)*rh)
    for i, r in enumerate(rows):
        for j, v in enumerate(r.split('|')):
            c.setFont(fb if i == 0 else f, 10); c.drawString(cols[j]+5, top-i*rh-14, v)
    y = top - len(rows)*rh - 40
    # borderless (stream) table
    c.setFont(fb, 12); c.drawString(60, y, 'Ключевые показатели'); y -= 20
    for i, r in enumerate(['Показатель|Q1|Q2|Q3', 'EPS|1,10|1,25|1,31', 'P/E|22,4|21,0|20,3', 'ROE, %|18,2|18,9|19,4']):
        for j, v in enumerate(r.split('|')):
            c.setFont(fb if i == 0 else f, 10); c.drawString([60, 220, 300, 380][j], y, v)
        y -= 16
    c.setLineWidth(0.5); c.line(60, y+30+16*3-2, 440, y+30+16*3-2)
    # image (large bitmap drawn small)
    img = Image.new('RGB', (600, 300), (40, 120, 200))
    for x in range(0, 600, 50):
        for yy in range(300): img.putpixel((x, yy), (255, 255, 255))
    c.drawImage(ImageReader(img), 60, y-150, width=200, height=100)
    # hyperlink
    c.setFont(f, 10); c.setFillColorRGB(0, 0, 1)
    c.drawString(300, y-80, 'Investor relations'); c.linkURL('https://example.com/ir', (300, y-84, 400, y-70))
    c.setFillColorRGB(0, 0, 0)
    # vector graphic (non iso-oriented)
    c.circle(480, y-100, 30, stroke=1, fill=0)
    c.showPage()
    # page 2: rotated, form xobject
    c.setPageRotation(90)
    c.beginForm('frm'); c.setFont(f, 12); c.drawString(70, 450, 'Текст внутри Form XObject'); c.rect(65, 440, 200, 25); c.endForm()
    c.doForm('frm')
    c.setFont(f, 12); c.drawString(70, 400, 'Повёрнутая страница, обычный текст')
    c.showPage(); c.save()

if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    make_report(os.path.join(OUT, 'report.pdf'))
    print('ok')
