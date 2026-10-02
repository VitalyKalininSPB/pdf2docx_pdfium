# Third-party notices

## pdf2docx

Large parts of this package (layout analysis, table detection, DOCX generation) are derived
from pdf2docx 0.5.13: https://github.com/ArtifexSoftware/pdf2docx

    Copyright (c) 2026 Artifex Software, Inc.
    Copyright (c) dothinking and pdf2docx contributors
    Licensed under the MIT License (full text in LICENSE).

Changes: the PyMuPDF backend was removed and replaced by a PDFium backend
(`backend/pdfium_doc.py`, `page/RawPagePdfium.py`, `image/ImagesExtractor.py`),
`fitz.Rect/Point/Matrix` were replaced by `common/geometry.py`, debug plotting
was reimplemented with Pillow, hyperlink XML generation was fixed.

## Runtime dependencies (not bundled)

| Package | License | URL |
|---|---|---|
| pypdfium2 | Apache-2.0 or BSD-3-Clause | https://github.com/pypdfium2-team/pypdfium2 |
| PDFium (shipped in pypdfium2 wheels) | BSD-3-Clause / Apache-2.0 | https://pdfium.googlesource.com/pdfium/ |
| python-docx | MIT | https://github.com/python-openxml/python-docx |
| fontTools | MIT | https://github.com/fonttools/fonttools |
| numpy | BSD-3-Clause | https://numpy.org |
| opencv-python-headless | Apache-2.0 (OpenCV), MIT (wrapper) | https://github.com/opencv/opencv-python |
| Pillow | MIT-CMU | https://python-pillow.org |
| fire | Apache-2.0 | https://github.com/google/python-fire |

When distributing a binary bundle, include the license texts of the bundled packages
(PDFium wheels also contain notices of PDFium's own third-party components).

No component of MuPDF / PyMuPDF is used.
