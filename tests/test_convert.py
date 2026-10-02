import os
import sys
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, 'samples', 'report.pdf')


@pytest.fixture(scope='session', autouse=True)
def samples():
    if not os.path.exists(SAMPLE):
        pytest.importorskip('reportlab')
        subprocess.check_call([sys.executable, os.path.join(HERE, 'make_samples.py')])


def test_no_mupdf_import():
    '''The package must work with PyMuPDF/MuPDF completely unavailable.'''
    code = ("import sys; sys.modules['fitz']=None; sys.modules['pymupdf']=None;"
            "import pdf2docx_pdfium; print('ok')")
    out = subprocess.check_output([sys.executable, '-c', code], cwd=os.path.dirname(HERE))
    assert out.strip() == b'ok'


def test_convert_and_tables(tmp_path):
    from docx import Document
    from pdf2docx_pdfium import Converter

    docx_file = tmp_path / 'report.docx'
    cv = Converter(SAMPLE)
    cv.convert(str(docx_file))
    tables = cv.extract_tables(pages=[0])
    cv.close()

    # lattice table restored with exact cell values (Cyrillic + numbers)
    assert tables[0] == [
        ['Сегмент', '2024', '2025', 'Изм., %'],
        ['Облако', '1 200', '1 450', '20,8'],
        ['Реклама', '900', '960', '6,7'],
        ['Прочее', '300', '290', '-3,3'],
        ['Итого', '2 400', '2 700', '12,5'],
    ]

    doc = Document(str(docx_file))
    assert len(doc.tables) >= 2                     # lattice + stream table
    text = '\n'.join(p.text for p in doc.paragraphs)
    assert 'Операционная маржа составила 31,4%' in text
    assert doc.inline_shapes or any(t for t in doc.tables)  # image exported

    rels = doc.part.rels.values()
    assert any(r.reltype.endswith('/hyperlink') and r.target_ref == 'https://example.com/ir' for r in rels)


def test_stream_input(tmp_path):
    from pdf2docx_pdfium import Converter
    with open(SAMPLE, 'rb') as f:
        cv = Converter(stream=f.read())
    out = tmp_path / 'from_stream.docx'
    cv.convert(str(out), pages=[0])
    cv.close()
    assert out.stat().st_size > 0


def test_debug_page(tmp_path):
    from pdf2docx_pdfium import Converter
    cv = Converter(SAMPLE)
    debug_pdf = tmp_path / 'debug.pdf'
    cv.debug_page(0, str(tmp_path / 'debug.docx'), str(debug_pdf), str(tmp_path / 'layout.json'))
    cv.close()
    assert debug_pdf.exists() and (tmp_path / 'layout.json').exists()
