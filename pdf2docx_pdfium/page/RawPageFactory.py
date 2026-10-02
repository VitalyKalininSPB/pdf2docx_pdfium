'''Load :py:class:`~pdf2docx_pdfium.page.RawPage` with the specified pdf engine.
Only the PDFium engine is implemented.'''

from .RawPagePdfium import RawPagePdfium


class RawPageFactory:

    MAP = {
        'PDFIUM': RawPagePdfium
    }

    @classmethod
    def create(cls, page_engine, backend: str = 'pdfium'):
        '''Create RawPage class with specified backend.'''
        klass = cls.MAP.get(backend.upper(), None)
        if not klass:
            raise TypeError(f'Page with pdf engine "{backend}" is not implemented yet.')
        return klass(page_engine=page_engine)
