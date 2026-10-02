from pdf2docx_pdfium.common.geometry import Rect, Point, Matrix, page_rotation_matrix


def test_rect_union_ignores_empty():
    assert tuple(Rect() | Rect(1, 1, 3, 3)) == (1, 1, 3, 3)
    assert tuple(Rect(1, 1, 3, 3) | Rect(5, 5, 5, 9)) == (1, 1, 3, 3)  # zero-width rect ignored


def test_rect_bool_and_size():
    assert not Rect()
    assert Rect(3, 3, 2, 2)  # invalid but non-zero -> truthy (PyMuPDF semantics)
    assert Rect(3, 3, 2, 2).width == 0 and Rect(3, 3, 2, 2).is_empty


def test_intersection_and_contains():
    a, b = Rect(0, 0, 10, 10), Rect(5, 5, 20, 20)
    assert tuple(a & b) == (5, 5, 10, 10)
    assert a.intersects(b) and not a.intersects(Rect(10, 0, 20, 10))
    assert Rect(1, 1, 2, 2) in a and (5, 5) in a and (10, 10) not in a


def test_rotation_matrix():
    m = page_rotation_matrix(300, 500, 90)
    assert tuple(m) == (0, 1, -1, 0, 500, 0)
    assert tuple(Point(10, 20) * m) == (480, 10)
    assert tuple(Rect(1, 2, 3, 4) * Matrix(0, 1, -1, 0, 842, 0)) == (838, 1, 840, 3)
    assert tuple(Point(1, 0) * Matrix(90)) == (0, 1)
