"""Extract images from PDF with the PDFium backend.

Both raster images and vector graphics are considered:

* Raster images are page objects of type *image* (also inside Form XObjects).
  Overlapping images are merged by clipping the page region.
* Vector graphics are composed of paths; their regions are detected by finding
  contours with ``opencv`` on a page rendering without text and images.
"""

import io
import logging

from ..common.Collection import Collection
from ..common.share import BlockType
from ..common.geometry import Rect
from ..common.algorithm import recursive_xy_cut, inner_contours, xy_project_profile


class ImagesExtractor:
    """Extract images from a :py:class:`~pdf2docx_pdfium.backend.pdfium_doc.PdfiumPage`."""

    def __init__(self, page) -> None:
        self._page = page

    # ------------------------------------------------------------------
    def clip_page_to_pixmap(self, bbox: Rect = None, rm_image: bool = False, zoom: float = 3.0):
        """Render page region (text always hidden) to a PIL image.

        Args:
            bbox (Rect, optional): Target area in un-rotated page CS. Entire page if None.
            rm_image (bool): Hide raster images as well.
            zoom (float): Resolution ratio to 72 dpi.
        """
        if bbox is None:
            clip = None
        else:
            clip = Rect(bbox) * self._page.rotation_matrix if self._page.rotation else Rect(bbox)
        return self._page.render(clip=clip, zoom=zoom, hide_text=True, hide_images=rm_image)

    def clip_page_to_dict(self, bbox: Rect = None, rm_image: bool = False, clip_image_res_ratio: float = 3.0):
        """Clip page (without text) according to ``bbox`` and convert to image raw dict."""
        pil = self.clip_page_to_pixmap(bbox=bbox, rm_image=rm_image, zoom=clip_image_res_ratio)
        if pil is None:
            return None
        return self._to_raw_dict(pil, bbox if bbox is not None else self._page.cropbox)

    # ------------------------------------------------------------------
    def extract_images(self, clip_image_res_ratio: float = 3.0):
        """Extract raster images placed on the page.

        Returns:
            list: A list of image raw dicts.
        """
        page = self._page
        page_bbox = page.cropbox

        # step 1: collect images
        ic = Collection()
        for image in page.get_images():
            bbox = image['bbox']
            if bbox.get_area() <= 4: continue            # ignore tiny images
            if not page_bbox.intersects(bbox): continue  # ignore images outside page
            ic.append((bbox, image))

        # step 2: group by intersection
        groups = ic.group(lambda a, b: a[0].intersects(b[0]))

        # step 3: check each group
        images = []
        for group in groups:
            if len(group) > 1:
                # several overlapping images (e.g. alpha masks) -> clip the union region
                clip_bbox = Rect()
                for bbox, _ in group: clip_bbox |= bbox
                raw_dict = self.clip_page_to_dict(clip_bbox, False, clip_image_res_ratio)
            else:
                bbox, image = group[0]
                raw_dict = None
                try:
                    res = page.image_to_png(image)
                except Exception as e:  # pragma: no cover - defensive
                    logging.warning('Failed to extract image, fallback to clipping page: %s', e)
                    res = None
                if res:
                    png, w, h = res
                    raw_dict = {'type': BlockType.IMAGE.value, 'bbox': tuple(bbox),
                                'width': w, 'height': h, 'image': png}
                    # image bytes are oriented in un-rotated page CS; rotate for page rotation
                    if page.rotation:
                        raw_dict['image'], raw_dict['width'], raw_dict['height'] = \
                            self._rotate_png(png, page.rotation)
                else:
                    raw_dict = self.clip_page_to_dict(bbox, False, clip_image_res_ratio)
            if raw_dict: images.append(raw_dict)

        return images

    # ------------------------------------------------------------------
    def detect_svg_contours(self, min_svg_gap_dx: float, min_svg_gap_dy: float, min_w: float, min_h: float):
        """Find contour of potential vector graphics.

        Returns:
            list: A list of potential svg region: (external_bbox, inner_bboxes:list).
        """
        import cv2 as cv
        import numpy as np

        pil = self.clip_page_to_pixmap(rm_image=True, zoom=1.0)
        src = cv.cvtColor(np.array(pil), cv.COLOR_RGB2BGR)

        gray = cv.cvtColor(src, cv.COLOR_BGR2GRAY)
        _, binary = cv.threshold(gray, 253, 255, cv.THRESH_BINARY_INV)

        external_bboxes = recursive_xy_cut(binary, min_dx=min_svg_gap_dx, min_dy=min_svg_gap_dy)
        grouped_inner_bboxes = [inner_contours(binary, bbox, min_w, min_h) for bbox in external_bboxes]
        return list(zip(external_bboxes, grouped_inner_bboxes))

    # ------------------------------------------------------------------
    @staticmethod
    def _to_raw_dict(pil, bbox: Rect):
        buf = io.BytesIO()
        pil.save(buf, format='PNG')
        return {
            'type': BlockType.IMAGE.value,
            'bbox': tuple(bbox),
            'width': pil.width,
            'height': pil.height,
            'image': buf.getvalue(),
        }

    @staticmethod
    def _rotate_png(png: bytes, rotation: int):
        '''Rotate PNG clockwise by ``rotation`` degrees -> (png, width, height).'''
        from PIL import Image as PILImage
        im = PILImage.open(io.BytesIO(png))
        im = im.rotate(-rotation, expand=True)
        buf = io.BytesIO()
        im.save(buf, format='PNG')
        return buf.getvalue(), im.width, im.height
