'''PDFium backend: thin wrapper around ``pypdfium2`` providing exactly the data
the layout engine needs (formerly obtained from PyMuPDF).

Coordinate conventions (same as the original PyMuPDF-based implementation):

* **un-rotated page CS**: origin at the top-left corner of the CropBox, y-axis down.
  Used for text blocks and raster images (``bbox`` keys of raw dicts).
* **real page CS**: un-rotated CS multiplied by ``rotation_matrix``, i.e. the page as
  displayed. Used for vector drawings (paths) and hyperlinks.
'''

import ctypes
import logging
import math

import pypdfium2 as pdfium
import pypdfium2.raw as R

from ..common.geometry import Rect, Point, Matrix, page_rotation_matrix


# MuPDF-compatible font flags used by ``TextSpan``
FLAG_SUPERSCRIPT = 1 << 0
FLAG_ITALIC = 1 << 1
FLAG_SERIFED = 1 << 2
FLAG_MONOSPACED = 1 << 3
FLAG_BOLD = 1 << 4

# PDF font descriptor flags (PDF 32000-1:2008, table 123)
_PDF_FIXED_PITCH = 1 << 0
_PDF_SERIF = 1 << 1
_PDF_ITALIC = 1 << 6
_PDF_FORCE_BOLD = 1 << 18

# MuPDF stext heuristics (relative to font size)
SPACE_DIST = 0.15        # gap that warrants inserting a space
SPACE_MAX_DIST = 0.8     # larger gap on the same baseline -> new line
BASELINE_DIST = 0.5      # perpendicular offset tolerated on the same line
PARAGRAPH_DIST = 1.6     # perpendicular offset that starts a new block

_TEXT_RENDER_INVISIBLE = 3


class PdfiumDocument:
    '''A PDF document opened with PDFium.'''

    def __init__(self, pdf_file=None, stream: bytes = None, password: str = None):
        source = stream if stream is not None else pdf_file
        try:
            self._doc = pdfium.PdfDocument(source, password=password or None)
        except pdfium.PdfiumError as e:
            msg = str(e).lower()
            if 'password' in msg:
                raise PermissionError('Require correct password to open the PDF.') from e
            raise
        self._pages = {}

    @property
    def raw(self): return self._doc

    def __len__(self): return len(self._doc)

    def __iter__(self):
        for i in range(len(self)):
            yield self[i]

    def __getitem__(self, index: int):
        if index < 0: index += len(self)
        if index not in self._pages:
            self._pages[index] = PdfiumPage(self, index)
        return self._pages[index]

    def close(self):
        for page in self._pages.values():
            page.close()
        self._pages.clear()
        if self._doc is not None:
            self._doc.close()
            self._doc = None


class PdfiumPage:
    '''A page wrapper exposing text, drawings, images, links and rendering.'''

    def __init__(self, doc: PdfiumDocument, index: int):
        self.parent = doc
        self.index = index
        self._page = doc.raw[index]
        self._textpage = None
        self._fonts = {}  # base font name -> font data bytes (or None)

        # geometry: CropBox in PDF user space (y-up)
        l, b, r, t = self._page.get_cropbox()
        self._crop = (l, b, r, t)
        self.unrotated_width = r - l
        self.unrotated_height = t - b
        self.rotation = int(self._page.get_rotation() or 0) % 360
        self.rotation_matrix = page_rotation_matrix(
            self.unrotated_width, self.unrotated_height, self.rotation)
        if self.rotation in (90, 270):
            self.width, self.height = self.unrotated_height, self.unrotated_width
        else:
            self.width, self.height = self.unrotated_width, self.unrotated_height

        # PDF user space -> un-rotated top-left page CS
        self.pdf_to_unrotated = Matrix(1, 0, 0, -1, -l, t)
        # PDF user space -> real (rotated) page CS
        self.pdf_to_real = self.pdf_to_unrotated * self.rotation_matrix

    # ------------------------------------------------------------------
    @property
    def raw(self): return self._page

    @property
    def rect(self): return Rect(0, 0, self.width, self.height)

    @property
    def cropbox(self): return Rect(0, 0, self.unrotated_width, self.unrotated_height)

    @property
    def textpage(self):
        if self._textpage is None:
            self._textpage = self._page.get_textpage()
        return self._textpage

    def close(self):
        if self._textpage is not None:
            self._textpage.close()
            self._textpage = None
        if self._page is not None:
            self._page.close()
            self._page = None

    # ------------------------------------------------------------------
    # page objects
    # ------------------------------------------------------------------
    def iter_objects(self, page=None, types=None):
        '''Yield ``(obj_handle, matrix_to_pdf_page)`` recursively, entering Form XObjects.

        ``matrix_to_pdf_page`` maps object-local coordinates (path points / image unit
        square) to PDF page user space.
        '''
        page = page or self._page
        stack = [(R.FPDFPage_GetObject(page.raw, i), Matrix()) for i in range(R.FPDFPage_CountObjects(page.raw))]
        stack.reverse()
        while stack:
            obj, parent_m = stack.pop()
            if not obj:
                continue
            t = R.FPDFPageObj_GetType(obj)
            m = R.FS_MATRIX()
            if R.FPDFPageObj_GetMatrix(obj, m):
                local = Matrix(m.a, m.b, m.c, m.d, m.e, m.f)
            else:
                local = Matrix()
            # NOTE: pdfium's matrix of an object inside a form is relative to the form
            total = local * parent_m
            if t == R.FPDF_PAGEOBJ_FORM:
                children = [(R.FPDFFormObj_GetObject(obj, i), total)
                            for i in range(R.FPDFFormObj_CountObjects(obj))]
                children.reverse()
                stack.extend(children)
                if types is None or t in types:
                    yield obj, t, total
                continue
            if types is None or t in types:
                yield obj, t, total

    # ------------------------------------------------------------------
    # text
    # ------------------------------------------------------------------
    def get_text_rawdict(self, ocr: int = 0):
        '''Build a PyMuPDF ``rawdict``-like structure: blocks -> lines -> spans -> chars.

        Args:
            ocr (int): 0 = ignore invisible text; 2 = keep invisible (OCR layer) text only.
        '''
        tp = self.textpage.raw
        n = R.FPDFText_CountChars(tp)
        to_u = self.pdf_to_unrotated
        page_rect = self.cropbox

        font_buf = ctypes.create_string_buffer(256)
        font_flags = ctypes.c_int()
        rect = R.FS_RECTF()
        ox, oy = ctypes.c_double(), ctypes.c_double()
        fm = R.FS_MATRIX()
        cr, cg, cb, ca = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        obj_cache = {}

        chars = []
        for i in range(n):
            code = R.FPDFText_GetUnicode(tp, i)
            if R.FPDFText_IsGenerated(tp, i) == 1:
                continue  # generated spaces / line breaks: we re-create spaces ourselves
            if code in (0x0D, 0x0A, 0xFFFE, 0xFFFF):
                continue
            if code == 0x02:
                code = 0x2D  # pdfium reports a line-end hyphen as 0x02
            if 0xDC00 <= code <= 0xDFFF:
                continue  # low surrogate: consumed with the preceding high surrogate
            if 0xD800 <= code <= 0xDBFF:
                low = R.FPDFText_GetUnicode(tp, i + 1) if i + 1 < n else 0
                if not 0xDC00 <= low <= 0xDFFF:
                    continue
                code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
            try:
                c = chr(code)
            except ValueError:
                continue

            # visibility (hidden OCR layer)
            obj = R.FPDFText_GetTextObject(tp, i)
            key = ctypes.cast(obj, ctypes.c_void_p).value if obj else None
            if key not in obj_cache:
                info = {'mode': 0, 'ascent': None, 'descent': None, 'stroke': None}
                if obj:
                    info['mode'] = R.FPDFTextObj_GetTextRenderMode(obj)
                    font = R.FPDFTextObj_GetFont(obj)
                    if font:
                        info['font'] = font
                obj_cache[key] = info
            info = obj_cache[key]
            invisible = info['mode'] == _TEXT_RENDER_INVISIBLE
            if (ocr == 2 and not invisible) or (ocr != 2 and invisible):
                continue

            if not R.FPDFText_GetLooseCharBox(tp, i, rect):
                continue
            bbox = Rect(rect.left, rect.top, rect.right, rect.bottom) * to_u
            if not bbox.intersects(page_rect) and not (bbox.width == 0 and bbox.y0 >= 0):
                continue  # clip to page (MuPDF TEXT_MEDIABOX_CLIP)

            R.FPDFText_GetCharOrigin(tp, i, ox, oy)
            origin = Point(ox.value, oy.value) * to_u

            # baseline direction in un-rotated top-left CS
            expansion = 1.0
            if R.FPDFText_GetMatrix(tp, i, fm):
                dx, dy = fm.a, -fm.b
                expansion = math.sqrt(abs(fm.a * fm.d - fm.b * fm.c)) or 1.0
            else:
                dx, dy = 1.0, 0.0
            norm = math.hypot(dx, dy) or 1.0
            direction = (round(dx / norm, 6), round(dy / norm, 6))

            # effective font size = font size in text state x matrix expansion (MuPDF semantics)
            size = (R.FPDFText_GetFontSize(tp, i) or 1.0) * expansion
            R.FPDFText_GetFontInfo(tp, i, font_buf, len(font_buf), font_flags)
            font_name = font_buf.value.decode('utf-8', errors='replace')
            weight = R.FPDFText_GetFontWeight(tp, i)

            color = 0
            if R.FPDFText_GetFillColor(tp, i, cr, cg, cb, ca):
                color = (cr.value << 16) + (cg.value << 8) + cb.value

            flags = self._mupdf_flags(font_name, font_flags.value, weight)

            # font metrics ratios (MuPDF: span ascender / descender)
            if info['ascent'] is None:
                asc, desc = 0.9, -0.2  # sensible defaults
                font = info.get('font')
                if font:
                    a, d = ctypes.c_float(), ctypes.c_float()
                    if R.FPDFFont_GetAscent(font, ctypes.c_float(1000.0), a) and a.value:
                        asc = a.value / 1000.0
                    if R.FPDFFont_GetDescent(font, ctypes.c_float(1000.0), d) and d.value:
                        desc = d.value / 1000.0
                    self._register_font(font, font_name)
                info['ascent'], info['descent'] = asc, desc

            chars.append({
                'c': c, 'bbox': bbox, 'origin': origin, 'dir': direction,
                'size': size, 'font': font_name, 'flags': flags, 'color': color,
                'ascender': info['ascent'], 'descender': info['descent'],
            })

        self._split_ligatures(chars)
        blocks = self._group_chars(chars)
        return {'width': self.unrotated_width, 'height': self.unrotated_height, 'blocks': blocks}

    @staticmethod
    def _split_ligatures(chars: list):
        '''Ligatures (e.g. "fl") are reported as several chars sharing one box and origin:
        split the box evenly so that the chars are laid out sequentially.'''
        i, n = 0, len(chars)
        while i < n:
            j = i + 1
            while j < n and chars[j]['origin'] == chars[i]['origin'] and chars[j]['bbox'] == chars[i]['bbox']:
                j += 1
            if j - i > 1:
                b = chars[i]['bbox']
                dx, dy = chars[i]['dir']
                k = j - i
                for m in range(k):
                    c = chars[i + m]
                    if abs(dx) >= abs(dy):
                        w = b.width / k
                        x0 = b.x0 + m * w if dx >= 0 else b.x1 - (m + 1) * w
                        c['bbox'] = Rect(x0, b.y0, x0 + w, b.y1)
                        ox = c['origin'].x + (m * w if dx >= 0 else -m * w)
                        c['origin'] = Point(ox, c['origin'].y)
                    else:
                        h = b.height / k
                        y0 = b.y0 + m * h if dy >= 0 else b.y1 - (m + 1) * h
                        c['bbox'] = Rect(b.x0, y0, b.x1, y0 + h)
                        oy = c['origin'].y + (m * h if dy >= 0 else -m * h)
                        c['origin'] = Point(c['origin'].x, oy)
            i = j

    @staticmethod
    def _mupdf_flags(font_name: str, pdf_flags: int, weight: int):
        name = font_name.upper()
        flags = 0
        if pdf_flags & _PDF_ITALIC or 'ITALIC' in name or 'OBLIQUE' in name:
            flags |= FLAG_ITALIC
        if pdf_flags & _PDF_SERIF:
            flags |= FLAG_SERIFED
        if pdf_flags & _PDF_FIXED_PITCH:
            flags |= FLAG_MONOSPACED
        if (weight and weight >= 600) or pdf_flags & _PDF_FORCE_BOLD or \
                'BOLD' in name or 'BLACK' in name or 'HEAVY' in name:
            flags |= FLAG_BOLD
        return flags

    def _register_font(self, font, font_name):
        if font_name in self._fonts:
            return
        data = None
        try:
            size = ctypes.c_size_t()
            if R.FPDFFont_GetFontData(font, None, 0, size) and size.value:
                buf = (ctypes.c_uint8 * size.value)()
                if R.FPDFFont_GetFontData(font, buf, size.value, size):
                    data = bytes(buf)
        except Exception:  # pragma: no cover - defensive
            data = None
        self._fonts[font_name] = data

    def get_fonts(self):
        '''Fonts used on this page: ``{base_font_name: font_file_bytes_or_None}``.
        Populated during :meth:`get_text_rawdict`.'''
        return dict(self._fonts)

    # ---------------- chars -> spans -> lines -> blocks ----------------
    @staticmethod
    def _group_chars(chars: list):
        '''Group chars following the MuPDF structured-text heuristics.'''
        blocks = []
        block = line = span = None
        pen = None  # end point of the last char along the baseline
        last = None

        def new_span(ch):
            return {'bbox': Rect(ch['bbox']), 'size': ch['size'], 'flags': ch['flags'],
                    'font': ch['font'], 'color': ch['color'], 'ascender': ch['ascender'],
                    'descender': ch['descender'], 'origin': tuple(ch['origin']), 'chars': []}

        def same_style(s, ch):
            return s['font'] == ch['font'] and abs(s['size'] - ch['size']) < 0.01 \
                and s['color'] == ch['color'] and s['flags'] == ch['flags']

        def add_char(ch):
            nonlocal span
            if span is None or not same_style(span, ch):
                span = new_span(ch)
                line['spans'].append(span)
            span['chars'].append({'c': ch['c'], 'bbox': tuple(ch['bbox']), 'origin': tuple(ch['origin'])})
            span['bbox'] |= ch['bbox']

        def start_line(ch):
            nonlocal line, span
            line = {'wmode': 0, 'dir': ch['dir'], 'bbox': None, 'spans': []}
            span = None
            block['lines'].append(line)

        def start_block(ch):
            nonlocal block
            block = {'type': 0, 'bbox': None, 'lines': []}
            blocks.append(block)
            start_line(ch)

        for ch in chars:
            size = max(ch['size'], 1.0)
            if block is None:
                start_block(ch)
            else:
                ndx, ndy = line['dir']
                if ch['dir'] != line['dir']:
                    start_line(ch)
                else:
                    ox, oy = ch['origin']
                    px, py = pen
                    dx, dy = ox - px, oy - py
                    along = dx * ndx + dy * ndy           # motion along the baseline
                    perp = -dx * ndy + dy * ndx           # motion perpendicular to it
                    ref = max(size, last['size'])
                    if abs(perp) < ref * BASELINE_DIST:
                        if along < -ref * SPACE_DIST * 2 or along > ref * SPACE_MAX_DIST:
                            start_line(ch)  # jump back or big gap -> separate line
                        elif along > ref * SPACE_DIST and last['c'] != ' ' and ch['c'] != ' ':
                            # synthesize a space filling the gap (MuPDF behaviour)
                            sx0 = min(px, ox) if ndx >= 0 else max(px, ox)
                            sp = dict(last)
                            sp['c'] = ' '
                            b = last['bbox']
                            if abs(ndx) >= abs(ndy):
                                sp['bbox'] = Rect(min(px, ox), b.y0, max(px, ox), b.y1)
                            else:
                                sp['bbox'] = Rect(b.x0, min(py, oy), b.x1, max(py, oy))
                            sp['origin'] = Point(px, py)
                            add_char(sp)
                    elif abs(perp) < ref * PARAGRAPH_DIST:
                        start_line(ch)
                    else:
                        start_block(ch)
            add_char(ch)
            # advance pen to the end of this char along the baseline
            b = ch['bbox']
            ndx, ndy = ch['dir']
            ox, oy = ch['origin']
            advance = (b.x1 - b.x0) if abs(ndx) >= abs(ndy) else (b.y1 - b.y0)
            pen = (ox + ndx * advance, oy + ndy * advance)
            last = ch

        # finalize bboxes & drop empty structures
        result = []
        for blk in blocks:
            lines = []
            for ln in blk['lines']:
                spans = [s for s in ln['spans'] if s['chars']]
                if not spans: continue
                lbox = Rect()
                for s in spans:
                    lbox |= s['bbox']
                    s['bbox'] = tuple(s['bbox'])
                PdfiumPage._mark_superscripts(spans)
                ln['spans'] = spans
                ln['bbox'] = tuple(lbox)
                lines.append(ln)
            if not lines: continue
            bbox = Rect()
            for ln in lines: bbox |= Rect(ln['bbox'])
            blk['lines'] = lines
            blk['bbox'] = tuple(bbox)
            result.append(blk)
        return result

    @staticmethod
    def _mark_superscripts(spans: list):
        '''Mark small raised spans as superscript (MuPDF flag bit 0).'''
        if len(spans) < 2: return
        main = max(spans, key=lambda s: len(s['chars']) * s['size'])
        base = main['origin'][1]
        for s in spans:
            if s is main: continue
            if s['size'] < 0.85 * main['size'] and base - s['origin'][1] > 0.2 * main['size']:
                s['flags'] |= FLAG_SUPERSCRIPT

    # ------------------------------------------------------------------
    # vector drawings
    # ------------------------------------------------------------------
    def get_drawings(self):
        '''Paths as PyMuPDF ``get_drawings()``-like dicts, in **real** page CS.'''
        paths = []
        x, y = ctypes.c_float(), ctypes.c_float()
        fill_mode, stroke = ctypes.c_int(), ctypes.c_int()
        w = ctypes.c_float()
        r, g, b, a = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()

        for obj, _, m in self.iter_objects(types={R.FPDF_PAGEOBJ_PATH}):
            if not R.FPDFPath_GetDrawMode(obj, fill_mode, stroke):
                continue
            is_fill, is_stroke = fill_mode.value != 0, bool(stroke.value)

            fill_color = stroke_color = None
            if is_fill and R.FPDFPageObj_GetFillColor(obj, r, g, b, a):
                if a.value == 0: is_fill = False
                else: fill_color = (r.value / 255.0, g.value / 255.0, b.value / 255.0)
            if is_stroke and R.FPDFPageObj_GetStrokeColor(obj, r, g, b, a):
                if a.value == 0: is_stroke = False
                else: stroke_color = (r.value / 255.0, g.value / 255.0, b.value / 255.0)
            if not (is_fill or is_stroke):
                continue

            to_real = m * self.pdf_to_real
            width = 0.0
            if is_stroke and R.FPDFPageObj_GetStrokeWidth(obj, w):
                scale = math.sqrt(abs(m.a * m.d - m.b * m.c)) or 1.0
                width = w.value * scale

            # collect sub-paths
            subpaths, current, start, cursor = [], [], None, None
            pending_bezier = []
            for i in range(R.FPDFPath_CountSegments(obj)):
                seg = R.FPDFPath_GetPathSegment(obj, i)
                if not seg: continue
                R.FPDFPathSegment_GetPoint(seg, x, y)
                pt = tuple(Point(x.value, y.value) * to_real)
                kind = R.FPDFPathSegment_GetType(seg)
                if kind == R.FPDF_SEGMENT_MOVETO:
                    if current: subpaths.append(current)
                    current, start, cursor, pending_bezier = [], pt, pt, []
                elif kind == R.FPDF_SEGMENT_LINETO:
                    if cursor is None: cursor = start = pt; continue
                    current.append(('l', cursor, pt)); cursor = pt
                elif kind == R.FPDF_SEGMENT_BEZIERTO:
                    pending_bezier.append(pt)
                    if len(pending_bezier) == 3:
                        c1, c2, end = pending_bezier
                        current.append(('c', cursor, c1, c2, end)); cursor = end
                        pending_bezier = []
                if R.FPDFPathSegment_GetClose(seg) and current and cursor != start:
                    current.append(('l', cursor, start)); cursor = start
            if current: subpaths.append(current)

            items = []
            for sp in subpaths:
                rect = self._as_rect(sp)
                if rect is not None: items.append(('re', rect))
                else: items.extend(sp)
            if not items:
                continue

            path_type = ('f' if is_fill else '') + ('s' if is_stroke else '')
            paths.append({
                'type': path_type,
                'items': items,
                'color': stroke_color,
                'fill': fill_color,
                'width': width,
                'closePath': False,
            })
        return paths

    @staticmethod
    def _as_rect(items):
        '''Detect an axis-aligned rectangle given as 4 connected line segments.'''
        if len(items) != 4 or any(it[0] != 'l' for it in items): return None
        pts = [it[1] for it in items]
        if items[-1][2] != pts[0]: return None
        xs = sorted({round(p[0], 3) for p in pts})
        ys = sorted({round(p[1], 3) for p in pts})
        if len(xs) != 2 or len(ys) != 2: return None
        for (x0, y0), (x1, y1) in [(it[1], it[2]) for it in items]:
            if abs(x0 - x1) > 1e-3 and abs(y0 - y1) > 1e-3: return None
        return (xs[0], ys[0], xs[1], ys[1])

    # ------------------------------------------------------------------
    # images
    # ------------------------------------------------------------------
    def get_images(self):
        '''Raster images placed on page: list of dict with keys
        ``bbox`` (un-rotated CS), ``rotation`` (image rotation in degrees), ``obj``.'''
        images = []
        for obj, _, m in self.iter_objects(types={R.FPDF_PAGEOBJ_IMAGE}):
            to_u = m * self.pdf_to_unrotated
            bbox = Rect(0, 0, 1, 1) * to_u
            # image rotation (unit square maps +x to image's x axis)
            angle = math.degrees(math.atan2(m.b, m.a))
            rotation = int(round(angle / 90.0) * 90) % 360
            images.append({'bbox': bbox, 'rotation': rotation, 'obj': obj, 'matrix': m})
        return images

    def image_to_png(self, image: dict):
        '''Return ``(png_bytes, width, height)`` of an image, oriented like the page view
        in **un-rotated** CS, applying soft masks when possible.'''
        from PIL import Image as PILImage
        import io

        obj = image['obj']
        pil = None
        # 1) rendered bitmap: masks applied, but resolution limited to page size
        rendered = None
        try:
            bm = R.FPDFImageObj_GetRenderedBitmap(self.parent.raw.raw, self._page.raw, obj)
            if bm:
                rendered = pdfium.PdfBitmap.from_raw(bm).to_pil()
        except Exception:
            rendered = None
        # 2) raw bitmap: full resolution, no mask
        raw_img = None
        try:
            bm = R.FPDFImageObj_GetBitmap(obj)
            if bm:
                raw_img = pdfium.PdfBitmap.from_raw(bm).to_pil()
        except Exception:
            raw_img = None

        if raw_img is not None and (rendered is None or
                                    raw_img.width * raw_img.height > 1.5 * rendered.width * rendered.height):
            pil = raw_img.convert('RGBA' if raw_img.mode in ('RGBA', 'LA', 'P') else 'RGB')
            # raw bitmap is in image space: apply flips/rotation of the placement matrix
            pil = self._orient(pil, image['matrix'])
        elif rendered is not None:
            pil = rendered
        else:
            return None

        # un-rotated CS: rendered bitmap is in un-rotated page orientation already
        buf = io.BytesIO()
        pil.save(buf, format='PNG')
        return buf.getvalue(), pil.width, pil.height

    @staticmethod
    def _orient(pil, m: Matrix):
        '''Orient a raw image bitmap (rows top->bottom) like it appears on the
        un-rotated page, using the multiple-of-90-degrees / mirroring part of the
        placement matrix ``m`` (unit square -> PDF user space).'''
        from PIL import Image as PILImage
        sign = lambda v: 0 if abs(v) < 1e-6 else (1 if v > 0 else -1)
        ux = (sign(m.a), sign(-m.b))   # image x axis in top-down page CS
        vy = (sign(-m.c), sign(m.d))   # image row axis (downwards) in top-down page CS
        table = {
            ((1, 0), (0, 1)): None,
            ((-1, 0), (0, 1)): PILImage.FLIP_LEFT_RIGHT,
            ((1, 0), (0, -1)): PILImage.FLIP_TOP_BOTTOM,
            ((-1, 0), (0, -1)): PILImage.ROTATE_180,
            ((0, 1), (-1, 0)): PILImage.ROTATE_270,
            ((0, -1), (1, 0)): PILImage.ROTATE_90,
            ((0, 1), (1, 0)): PILImage.TRANSPOSE,
            ((0, -1), (-1, 0)): PILImage.TRANSVERSE,
        }
        op = table.get((ux, vy))
        return pil.transpose(op) if op is not None else pil

    # ------------------------------------------------------------------
    # links
    # ------------------------------------------------------------------
    def get_links(self):
        '''External (URI) links: list of ``{'from': Rect (real CS), 'uri': str}``.'''
        links = []
        pos = ctypes.c_int(0)
        link = R.FPDF_LINK()
        rect = R.FS_RECTF()
        doc = self.parent.raw.raw
        while R.FPDFLink_Enumerate(self._page.raw, pos, link):
            action = R.FPDFLink_GetAction(link)
            if not action or R.FPDFAction_GetType(action) != R.PDFACTION_URI:
                continue
            n = R.FPDFAction_GetURIPath(doc, action, None, 0)
            if n <= 0: continue
            buf = ctypes.create_string_buffer(n)
            R.FPDFAction_GetURIPath(doc, action, buf, n)
            uri = buf.value.decode('utf-8', errors='replace')
            if not R.FPDFLink_GetAnnotRect(link, rect):
                continue
            bbox = Rect(rect.left, rect.top, rect.right, rect.bottom) * self.pdf_to_real
            links.append({'from': bbox, 'uri': uri})
        return links

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def render(self, clip: Rect = None, zoom: float = 1.0, hide_text: bool = False, hide_images: bool = False):
        '''Render page (real CS) to a PIL RGB image, optionally clipped and with text/images hidden.

        Text/images are hidden on a separate, temporary page instance, so the document is
        never modified.
        '''
        page = self._page
        temp = None
        if hide_text or hide_images:
            temp = self.parent.raw.get_page(self.index)
            page = temp
            to_remove = []
            for obj, t, _ in self.iter_objects(page=temp):
                if t == R.FPDF_PAGEOBJ_TEXT and hide_text:
                    R.FPDFTextObj_SetTextRenderMode(obj, _TEXT_RENDER_INVISIBLE)
                elif t == R.FPDF_PAGEOBJ_IMAGE and hide_images:
                    R.FPDFPageObj_SetIsActive(obj, False)
        try:
            W, H = self.width, self.height
            if clip is None:
                crop = (0, 0, 0, 0)
            else:
                c = Rect(clip) & Rect(0, 0, W, H)
                if c.is_empty:
                    return None
                crop = (c.x0, H - c.y1, W - c.x1, c.y0)
            bitmap = page.render(scale=zoom, crop=crop, draw_annots=True, may_draw_forms=True)
            return bitmap.to_pil().convert('RGB')
        finally:
            if temp is not None:
                temp.close()
