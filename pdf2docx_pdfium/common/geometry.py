'''Minimal 2D geometry primitives used across the library.

This module replaces ``fitz.Rect``, ``fitz.Point`` and ``fitz.Matrix`` which were
previously provided by PyMuPDF. Only the subset of behaviour actually used by the
layout analysis code is implemented, but the semantics intentionally mirror PyMuPDF
(e.g. union ignores empty rectangles, ``bool(Rect())`` is ``False``, ``width`` and
``height`` are never negative) so that the ported layout heuristics behave the same.

Coordinate system: origin at the top-left corner of the page, y-axis pointing down,
units are PDF points (1/72 inch).
'''

import math


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


class Matrix:
    '''Affine transformation ``[a b c d e f]``: ``x' = a*x + c*y + e``, ``y' = b*x + d*y + f``.

    Constructors (compatible with ``fitz.Matrix``)::

        Matrix()                 # identity
        Matrix(deg)              # pure rotation by ``deg`` degrees
        Matrix(sx, sy)           # scaling
        Matrix(a, b, c, d, e, f)
        Matrix(seq_of_6)
    '''
    __slots__ = ('a', 'b', 'c', 'd', 'e', 'f')

    def __init__(self, *args):
        if not args:
            vals = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        elif len(args) == 1 and _is_number(args[0]):
            deg = float(args[0]) % 360
            # exact values for the multiples of 90 degrees
            exact = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}
            if deg in exact:
                cos, sin = exact[deg]
            else:
                rad = math.radians(deg)
                cos, sin = math.cos(rad), math.sin(rad)
            vals = (cos, sin, -sin, cos, 0.0, 0.0)
        elif len(args) == 1:
            vals = tuple(args[0])
        elif len(args) == 2:
            vals = (args[0], 0.0, 0.0, args[1], 0.0, 0.0)
        elif len(args) == 6:
            vals = args
        else:
            raise ValueError(f'Matrix: bad arguments {args}')
        if len(vals) != 6:
            raise ValueError('Matrix: bad sequence length')
        self.a, self.b, self.c, self.d, self.e, self.f = map(float, vals)

    def __iter__(self):
        return iter((self.a, self.b, self.c, self.d, self.e, self.f))

    def __len__(self): return 6

    def __getitem__(self, i): return tuple(self)[i]

    def __eq__(self, other):
        try:
            return tuple(self) == tuple(Matrix(other) if not isinstance(other, Matrix) else other)
        except Exception:
            return False

    def __hash__(self): return hash(tuple(self))

    def __bool__(self):
        # truthy unless it is the zero matrix (PyMuPDF semantics)
        return any(v != 0 for v in self)

    def __mul__(self, other):
        '''Concatenation ``self x other``: first apply ``self``, then ``other``.'''
        m = other if isinstance(other, Matrix) else Matrix(other)
        return Matrix(
            self.a * m.a + self.b * m.c,
            self.a * m.b + self.b * m.d,
            self.c * m.a + self.d * m.c,
            self.c * m.b + self.d * m.d,
            self.e * m.a + self.f * m.c + m.e,
            self.e * m.b + self.f * m.d + m.f)

    @property
    def is_rectilinear(self):
        return (abs(self.b) < 1e-6 and abs(self.c) < 1e-6) or \
               (abs(self.a) < 1e-6 and abs(self.d) < 1e-6)

    def inverted(self):
        det = self.a * self.d - self.b * self.c
        if abs(det) < 1e-12:
            return None
        a, b, c, d = self.d / det, -self.b / det, -self.c / det, self.a / det
        e = -(self.e * a + self.f * c)
        f = -(self.e * b + self.f * d)
        return Matrix(a, b, c, d, e, f)

    def __repr__(self): return f'Matrix{tuple(self)}'


class Point:
    '''2D point / vector.'''
    __slots__ = ('x', 'y')

    def __init__(self, *args):
        if not args:
            x, y = 0.0, 0.0
        elif len(args) == 1:
            x, y = args[0]
        elif len(args) == 2:
            x, y = args
        else:
            raise ValueError('Point: bad arguments')
        self.x, self.y = float(x), float(y)

    def __iter__(self): return iter((self.x, self.y))

    def __len__(self): return 2

    def __getitem__(self, i): return (self.x, self.y)[i]

    def __eq__(self, other):
        try:
            return len(other) == 2 and self.x == other[0] and self.y == other[1]
        except TypeError:
            return False

    def __hash__(self): return hash((self.x, self.y))

    def __add__(self, p): return Point(self.x + p[0], self.y + p[1])

    def __sub__(self, p): return Point(self.x - p[0], self.y - p[1])

    def __mul__(self, m):
        if _is_number(m):
            return Point(self.x * m, self.y * m)
        m = m if isinstance(m, Matrix) else Matrix(m)
        return Point(self.x * m.a + self.y * m.c + m.e,
                     self.x * m.b + self.y * m.d + m.f)

    def __abs__(self): return math.hypot(self.x, self.y)

    def __bool__(self): return not (self.x == 0 and self.y == 0)

    def transform(self, m):
        p = self * m
        self.x, self.y = p.x, p.y
        return self

    def __repr__(self): return f'Point({self.x}, {self.y})'


class Rect:
    '''Axis-aligned rectangle ``(x0, y0, x1, y1)``.

    Constructors (compatible with ``fitz.Rect``)::

        Rect()                      # (0, 0, 0, 0) -> falsy
        Rect(x0, y0, x1, y1)
        Rect(seq_of_4) / Rect(rect)
        Rect(point_tl, point_br)
    '''
    __slots__ = ('x0', 'y0', 'x1', 'y1')

    def __init__(self, *args):
        if not args:
            vals = (0.0, 0.0, 0.0, 0.0)
        elif len(args) == 1:
            vals = tuple(args[0])
        elif len(args) == 2:
            vals = (*tuple(args[0]), *tuple(args[1]))
        elif len(args) == 4:
            vals = args
        else:
            raise ValueError(f'Rect: bad arguments {args}')
        if len(vals) != 4:
            raise ValueError('Rect: bad sequence length')
        self.x0, self.y0, self.x1, self.y1 = map(float, vals)

    # ---------------- sequence protocol ----------------
    def __iter__(self): return iter((self.x0, self.y0, self.x1, self.y1))

    def __len__(self): return 4

    def __getitem__(self, i): return (self.x0, self.y0, self.x1, self.y1)[i]

    def __repr__(self): return f'Rect{tuple(self)}'

    def __eq__(self, other):
        try:
            return len(other) == 4 and tuple(self) == tuple(float(v) for v in other)
        except TypeError:
            return False

    def __hash__(self): return hash(tuple(self))

    def __bool__(self):
        # PyMuPDF: only the all-zero rectangle is falsy
        return not (self.x0 == 0 and self.y0 == 0 and self.x1 == 0 and self.y1 == 0)

    # ---------------- properties ----------------
    @property
    def width(self): return max(self.x1 - self.x0, 0.0)

    @property
    def height(self): return max(self.y1 - self.y0, 0.0)

    @property
    def tl(self): return Point(self.x0, self.y0)

    @property
    def br(self): return Point(self.x1, self.y1)

    top_left, bottom_right = tl, br

    @property
    def is_empty(self): return self.x0 >= self.x1 or self.y0 >= self.y1

    @property
    def is_valid(self): return self.x0 <= self.x1 and self.y0 <= self.y1

    @property
    def is_infinite(self): return False

    def get_area(self, *args): return self.width * self.height

    # ---------------- operations ----------------
    def normalize(self):
        if self.x0 > self.x1: self.x0, self.x1 = self.x1, self.x0
        if self.y0 > self.y1: self.y0, self.y1 = self.y1, self.y0
        return self

    def include_rect(self, r):
        r = r if isinstance(r, Rect) else Rect(r)
        if r.is_empty:
            return self
        if self.is_empty:
            self.x0, self.y0, self.x1, self.y1 = r
            return self
        self.x0, self.y0 = min(self.x0, r.x0), min(self.y0, r.y0)
        self.x1, self.y1 = max(self.x1, r.x1), max(self.y1, r.y1)
        return self

    def include_point(self, p):
        x, y = p
        self.x0, self.y0 = min(self.x0, x), min(self.y0, y)
        self.x1, self.y1 = max(self.x1, x), max(self.y1, y)
        return self

    def intersect(self, r):
        '''In-place intersection.'''
        r = r if isinstance(r, Rect) else Rect(r)
        if self.is_empty:
            return self
        if r.is_empty:
            self.x0, self.y0, self.x1, self.y1 = r
            return self
        self.x0, self.y0 = max(self.x0, r.x0), max(self.y0, r.y0)
        self.x1, self.y1 = min(self.x1, r.x1), min(self.y1, r.y1)
        return self

    def intersects(self, r):
        r = r if isinstance(r, Rect) else Rect(r)
        if self.is_empty or r.is_empty:
            return False
        return self.x0 < r.x1 and r.x0 < self.x1 and self.y0 < r.y1 and r.y0 < self.y1

    def contains(self, x): return self.__contains__(x)

    def __contains__(self, x):
        if _is_number(x):
            return x in tuple(self)
        if len(x) == 2:
            px, py = x
            return self.x0 <= px < self.x1 and self.y0 <= py < self.y1
        r = x if isinstance(x, Rect) else Rect(x)
        return self.x0 <= r.x0 <= r.x1 <= self.x1 and self.y0 <= r.y0 <= r.y1 <= self.y1

    def __or__(self, x):
        r = Rect(self)
        if len(x) == 2:
            return r.include_point(x)
        return r.include_rect(x)

    def __ior__(self, x):
        if len(x) == 2:
            return self.include_point(x)
        return self.include_rect(x)

    def __and__(self, x): return Rect(self).intersect(x)

    def __iand__(self, x): return self.intersect(x)

    def __add__(self, x):
        if _is_number(x):
            return Rect(self.x0 + x, self.y0 + x, self.x1 + x, self.y1 + x)
        a, b, c, d = x
        return Rect(self.x0 + a, self.y0 + b, self.x1 + c, self.y1 + d)

    __radd__ = __add__

    def __iadd__(self, x):
        r = self + x
        self.x0, self.y0, self.x1, self.y1 = r
        return self

    def __sub__(self, x):
        if _is_number(x):
            return self + (-x)
        a, b, c, d = x
        return self + (-a, -b, -c, -d)

    def __mul__(self, m):
        if _is_number(m):
            return Rect(self.x0 * m, self.y0 * m, self.x1 * m, self.y1 * m)
        return self.transformed(m)

    def __imul__(self, m):
        r = self * m
        self.x0, self.y0, self.x1, self.y1 = r
        return self

    def transformed(self, m):
        m = m if isinstance(m, Matrix) else Matrix(m)
        pts = [Point(self.x0, self.y0) * m, Point(self.x1, self.y0) * m,
               Point(self.x0, self.y1) * m, Point(self.x1, self.y1) * m]
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        return Rect(min(xs), min(ys), max(xs), max(ys))

    def transform(self, m):
        r = self.transformed(m)
        self.x0, self.y0, self.x1, self.y1 = r
        return self

    def round(self):
        return tuple(int(round(v)) for v in self)


def sRGB_to_pdf(srgb: int):
    '''sRGB integer -> (r, g, b) float components in range [0, 1].'''
    return ((srgb >> 16) & 255) / 255.0, ((srgb >> 8) & 255) / 255.0, (srgb & 255) / 255.0


def page_rotation_matrix(width: float, height: float, rotation: int) -> Matrix:
    '''Matrix converting un-rotated page coordinates to the rotated (displayed) page.

    ``width`` / ``height`` are the dimensions of the **un-rotated** page.
    Mirrors ``fitz.Page.rotation_matrix``.
    '''
    rotation = int(rotation or 0) % 360
    if rotation == 90:
        return Matrix(0, 1, -1, 0, height, 0)
    if rotation == 180:
        return Matrix(-1, 0, 0, -1, width, height)
    if rotation == 270:
        return Matrix(0, -1, 1, 0, 0, width)
    return Matrix()
