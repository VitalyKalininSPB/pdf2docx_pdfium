'''Tiny raster canvas used to plot layout information in debug mode.

It mimics the handful of PyMuPDF drawing calls used by ``plot()`` methods
(``draw_rect``, ``draw_line``, ``insert_text``, ``new_shape``) and saves all pages
as a multi-page PDF with Pillow. No PDF library is required.
'''

from PIL import Image, ImageDraw

_SCALE = 2.0  # pixels per point


def _rgb(color, alpha=255):
    if color is None: return None
    r, g, b = (int(max(0, min(1, c)) * 255) for c in color[:3])
    return (r, g, b, alpha)


class DebugShape:
    '''Path builder compatible with ``fitz.Shape`` subset.'''

    def __init__(self, page):
        self._page = page
        self._items = []

    def draw_line(self, p1, p2): self._items.append(('l', [tuple(p1), tuple(p2)]))

    def draw_rect(self, rect): self._items.append(('re', tuple(rect)))

    def draw_quad(self, quad): self._items.append(('l', [tuple(p) for p in quad]))

    def draw_bezier(self, p1, p2, p3, p4): self._items.append(('l', [tuple(p1), tuple(p4)]))

    def finish(self, fill=None, color=None, width=1, **kwargs):
        for kind, data in self._items:
            if kind == 're':
                self._page.draw_rect(data, color=color, fill=fill, width=width)
            else:
                for a, b in zip(data[:-1], data[1:]):
                    self._page.draw_line(a, b, color=color or (0, 0, 0), width=width)
        self._items = []

    def commit(self, *args, **kwargs): pass


class DebugPage:
    def __init__(self, width: float, height: float):
        self.width, self.height = width, height
        self.image = Image.new('RGB', (max(1, int(width * _SCALE)), max(1, int(height * _SCALE))), 'white')
        self._draw = ImageDraw.Draw(self.image, 'RGBA')

    @staticmethod
    def _xy(p): return (p[0] * _SCALE, p[1] * _SCALE)

    def draw_rect(self, rect, color=None, fill=None, width=0.5, fill_opacity=1.0, **kwargs):
        x0, y0, x1, y1 = rect
        box = [x0 * _SCALE, y0 * _SCALE, max(x0, x1) * _SCALE, max(y0, y1) * _SCALE]
        self._draw.rectangle(box, fill=_rgb(fill, int(255 * fill_opacity)) if fill else None,
                             outline=_rgb(color) if color else None,
                             width=max(1, int((width or 0) * _SCALE)) if color else 0)

    def draw_line(self, p1, p2, color=(0, 0, 0), width=1, **kwargs):
        self._draw.line([self._xy(p1), self._xy(p2)], fill=_rgb(color), width=max(1, int(width * _SCALE)))

    def insert_text(self, point, text, color=(0, 0, 0), fontsize=11, **kwargs):
        self._draw.text(self._xy(point), text, fill=_rgb(color))

    def new_shape(self): return DebugShape(self)


class DebugDocument:
    '''Collection of debug pages, saved as one PDF.'''

    def __init__(self): self.pages = []

    def new_page(self, width: float, height: float):
        page = DebugPage(width, height)
        self.pages.append(page)
        return page

    def save(self, filename: str):
        if not self.pages or not filename: return
        first, *rest = [p.image for p in self.pages]
        first.save(filename, 'PDF', save_all=True, append_images=rest, resolution=72 * _SCALE)
