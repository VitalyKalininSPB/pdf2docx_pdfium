'''PDFium page engine: extract source contents with ``pypdfium2``.'''

from .RawPage import RawPage
from ..image.ImagesExtractor import ImagesExtractor
from ..shape.Paths import Paths
from ..common.Element import Element
from ..common.share import RectType, debug_plot


class RawPagePdfium(RawPage):
    '''A wrapper of :py:class:`~pdf2docx_pdfium.backend.pdfium_doc.PdfiumPage`.'''

    def extract_raw_dict(self, **settings):
        raw_dict = {}
        if not self.page_engine: return raw_dict

        # actual page size (rotation considered)
        w, h = self.page_engine.width, self.page_engine.height
        raw_dict.update({'width': w, 'height': h})
        self.width, self.height = w, h

        # Element is a base class processing coordinates, so set rotation matrix globally
        # NOTE: set before restoring any element
        Element.set_rotation_matrix(self.page_engine.rotation_matrix)

        raw_dict['blocks'] = self._preprocess_text(**settings)
        raw_dict['blocks'].extend(self._preprocess_images(**settings))

        shapes, images = self._preprocess_shapes(**settings)
        raw_dict['shapes'] = shapes
        raw_dict['blocks'].extend(images)

        raw_dict['shapes'].extend(self._preprocess_hyperlinks())
        return raw_dict

    def _preprocess_text(self, **settings):
        '''Extract text. Invisible text (render mode 3) is ignored if ``ocr=0``,
        while only invisible text is kept if ``ocr=2`` (OCR-ed PDF).'''
        ocr = settings.get('ocr', 0)
        if ocr == 1: raise SystemExit('OCR feature is planned but not implemented yet.')
        raw = self.page_engine.get_text_rawdict(ocr=ocr)
        return raw.get('blocks', [])

    def _preprocess_images(self, **settings):
        if settings.get('ocr', 0) == 2: return []
        return ImagesExtractor(self.page_engine).extract_images(settings['clip_image_res_ratio'])

    def _preprocess_shapes(self, **settings):
        '''Identify iso-oriented paths and convert vector graphic paths to pixmap.'''
        paths = self._init_paths(**settings)
        return paths.to_shapes_and_images(
            settings['min_svg_gap_dx'],
            settings['min_svg_gap_dy'],
            settings['min_svg_w'],
            settings['min_svg_h'],
            settings['clip_image_res_ratio'])

    @debug_plot('Source Paths')
    def _init_paths(self, **settings):
        raw_paths = self.page_engine.get_drawings()
        return Paths(parent=self).restore(raw_paths)

    def _preprocess_hyperlinks(self):
        return [{'type': RectType.HYPERLINK.value, 'bbox': tuple(link['from']), 'uri': link['uri']}
                for link in self.page_engine.get_links()]
