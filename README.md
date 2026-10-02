# pdf2docx_pdfium

Конвертер **PDF → DOCX** с восстановлением таблиц, абзацев, изображений и гиперссылок —
порт библиотеки [pdf2docx](https://github.com/ArtifexSoftware/pdf2docx) с **PyMuPDF/MuPDF на PDFium**
(через [pypdfium2](https://github.com/pypdfium2-team/pypdfium2)).

Зачем: `pdf2docx` распространяется по MIT, но зависит от PyMuPDF (AGPL-3.0 или коммерческая
лицензия Artifex). Этот порт полностью убирает MuPDF из цепочки зависимостей — остаются только
пакеты с разрешительными лицензиями, что подходит для закрытых коммерческих desktop-приложений.

| Зависимость | Лицензия |
|---|---|
| pypdfium2 / PDFium | Apache-2.0 / BSD-3-Clause |
| python-docx | MIT |
| fontTools | MIT |
| numpy | BSD-3-Clause |
| opencv-python-headless | Apache-2.0 |
| Pillow | MIT-CMU (HPND) |
| fire (только CLI) | Apache-2.0 |

## Установка

```bash
pip install git+https://github.com/VitalyKalininSPB/pdf2docx_pdfium.git
# или из исходников
pip install -e ".[dev]"
```

## Использование

API совместим с `pdf2docx` — меняется только имя пакета:

```python
from pdf2docx_pdfium import Converter

cv = Converter("report.pdf")            # или Converter(stream=pdf_bytes), password="..."
cv.convert("report.docx")               # все страницы; можно start=, end=, pages=[0, 2]
tables = cv.extract_tables(pages=[0])   # таблицы как list[list[str]]
cv.close()
```

Командная строка:

```bash
pdf2docx-pdfium convert report.pdf report.docx
pdf2docx-pdfium convert report.pdf --pages=1,3
pdf2docx-pdfium table report.pdf --start=0 --end=2
pdf2docx-pdfium debug report.pdf --page=0     # PDF с разметкой блоков + layout.json
```

Все параметры `Converter.default_settings` (пороги распознавания таблиц, `multi_processing`,
`parse_stream_table` и т. д.) сохранены без изменений.

## Как устроено

```text
PDF ──► PDFium (pypdfium2)                       pdf2docx_pdfium/backend/pdfium_doc.py
          ├─ символы: unicode, loose box, origin, матрица, шрифт, цвет, режим рендера
          ├─ path-объекты (в т.ч. внутри Form XObject) → линии / прямоугольники / кривые
          ├─ image-объекты → PNG (полное разрешение, ориентация по матрице)
          ├─ ссылки (URI-аннотации)
          └─ рендер страницы без текста/картинок (для векторной графики)
     ──► raw dict в формате PyMuPDF "rawdict" / get_drawings()
     ──► layout-анализ pdf2docx (без изменений): строки, абзацы, секции,
         lattice- и stream-таблицы, объединённые ячейки, заливки, подчёркивания
     ──► python-docx ──► DOCX
```

Что было заменено по сравнению с оригиналом:

* `common/geometry.py` — собственные `Rect` / `Point` / `Matrix` вместо `fitz.*`
  с той же семантикой (объединение игнорирует пустые прямоугольники, `bool(Rect())==False` и т. п.).
  Поведение сверено с PyMuPDF на 20 000 случайных случаях.
* `backend/pdfium_doc.py` — новый извлекающий слой: группировка символов в span → line → block
  по эвристикам MuPDF stext (вставка пробелов, разрыв строк по большим промежуткам,
  разбор лигатур, суррогатные пары, перенос с дефисом), флаги шрифта (bold/italic/serif/mono,
  верхний индекс), метрики ascent/descent, сбор встроенных шрифтов для fontTools.
* `page/RawPagePdfium.py`, `image/ImagesExtractor.py` — извлечение текста, изображений,
  векторной графики и ссылок через PDFium. Скрытие текста/изображений для рендера делается
  на временной копии страницы в памяти — документ не модифицируется.
* `common/debug_canvas.py` — режим отладки рисует разметку через Pillow вместо MuPDF.
* Скрытый текст (Tr 3, OCR-слой) фильтруется по каждому символу, а не по целому блоку.

Исправления относительно оригинала:

* гиперссылки: `w:hyperlink` вставляется в абзац, а не внутрь `w:r` — иначе LibreOffice
  и часть версий Word теряли текст ссылки;
* векторные фигуры на повёрнутых страницах (`/Rotate`) переводятся в ту же систему координат,
  что и текст.

## Качество: сравнение с оригинальным pdf2docx

На тестовом наборе самого pdf2docx (33 PDF) сравнивалась распарсенная структура
(блоки, таблицы, их размеры, текст) — `tools/compare_with_pdf2docx.py`:

* большинство файлов дают **идентичную структуру и текст**;
* остальные отличаются в основном пробелами (PDFium/наш группировщик вставляет пробелы
  между словами там, где MuPDF их «склеивал», например `Anillustrationof…` → `An illustration of…`)
  и в отдельных случаях — границами stream-таблиц рядом с векторными графиками.

## Ограничения

Те же, что у pdf2docx:

* сканы без текстового слоя не распознаются (нужен внешний OCR);
* текст, идущий сверху вниз (например, на странице с `/Rotate 90`), отбрасывается
  layout-движком pdf2docx — поддерживается только горизонтальный текст и вертикальный снизу вверх;
* сложные многоколоночные макеты и таблицы без линий восстанавливаются эвристически.

Специфика PDFium:

* у изображений с мягкой маской (SMask) в полном разрешении прозрачность теряется;
  если рендер PDFium с маской имеет сопоставимое разрешение — используется он;
* для шрифтов без встроенных данных высота строки берётся из метрик PDFium, а не fontTools.

## Разработка

```bash
pip install -e ".[dev]"
python tests/make_samples.py   # тестовые PDF (reportlab)
pytest -q
```

## Лицензия

MIT — см. [LICENSE](LICENSE). Исходный код layout-движка взят из pdf2docx
(© Artifex Software, Inc., © dothinking и контрибьюторы pdf2docx, MIT), уведомления об
авторских правах сохранены. Сведения о сторонних компонентах — в
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
