"""Exact polygon predicates for copper regions. Standard library only.

Copper questions are polygon questions, and the answers must be exact rather than
approximate: the first isolation-barrier check compared zone **bounding boxes**, which
accepts a non-rectangular pour that in fact crosses the barrier, and rejects an L-shaped
one that does not. Every predicate here works on the real outline, with holes.

A region is {"outline": [(x, y), ...], "holes": [[(x, y), ...], ...]} in mm.
A filled zone on one layer is a list of such regions (KiCad's SHAPE_POLY_SET).
"""
EPS = 1e-9


def point_in_poly(pt, poly):
    """Crossing-number test. A point exactly on an edge counts as inside."""
    x, y = pt
    n = len(poly)
    if n < 3:
        return False
    inside = False
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if _on_segment(x, y, x1, y1, x2, y2):
            return True
        if (y1 > y) != (y2 > y):
            xc = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xc:
                inside = not inside
    return inside


def _on_segment(px, py, x1, y1, x2, y2):
    cross = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
    if abs(cross) > 1e-7:
        return False
    return (min(x1, x2) - EPS <= px <= max(x1, x2) + EPS
            and min(y1, y2) - EPS <= py <= max(y1, y2) + EPS)


def point_in_region(pt, region):
    if not point_in_poly(pt, region["outline"]):
        return False
    return not any(point_in_poly(pt, h) for h in region.get("holes", ()))


def point_in_any(pt, regions):
    return any(point_in_region(pt, r) for r in regions)


def _proper_cross(a, b, c, d):
    """True when segment ab properly crosses cd (touching at a shared point is not a crossing)."""
    d1 = _side(c, d, a)
    d2 = _side(c, d, b)
    d3 = _side(a, b, c)
    d4 = _side(a, b, d)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _side(p, q, r):
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


def seg_crosses_poly(a, b, poly):
    n = len(poly)
    return any(_proper_cross(a, b, poly[i], poly[(i + 1) % n]) for i in range(n))


def seg_inside_region(a, b, region):
    """Exact for straight segments: both ends inside, and no edge properly crossed."""
    if not (point_in_region(a, region) and point_in_region(b, region)):
        return False
    if seg_crosses_poly(a, b, region["outline"]):
        return False
    return not any(seg_crosses_poly(a, b, h) for h in region.get("holes", ()))


def seg_inside_any(a, b, regions):
    """A segment fully backed by one region. A segment spanning a gap between two
    regions is **not** inside either: that gap is exactly a broken reference plane."""
    return any(seg_inside_region(a, b, r) for r in regions)


def seg_touches_poly(a, b, poly):
    """Segment overlaps the polygon's interior or boundary at all (intrusion test)."""
    if point_in_poly(a, poly) or point_in_poly(b, poly):
        return True
    n = len(poly)
    for i in range(n):
        c, d = poly[i], poly[(i + 1) % n]
        if _proper_cross(a, b, c, d) or _touch(a, b, c, d):
            return True
    return False


def _touch(a, b, c, d):
    for p, (q, r) in ((a, (c, d)), (b, (c, d)), (c, (a, b)), (d, (a, b))):
        if _on_segment(p[0], p[1], q[0], q[1], r[0], r[1]):
            return True
    return False


def dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def point_segment_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    den = dx * dx + dy * dy
    t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / den)) if den else 0
    return dist(p, (a[0] + t * dx, a[1] + t * dy))


def stroke_touches_poly(a, b, radius, polygon):
    """Round-ended copper segment against a polygon, including its full width."""
    if seg_touches_poly(a, b, polygon):
        return True
    for i, c in enumerate(polygon):
        d = polygon[(i + 1) % len(polygon)]
        if min(point_segment_distance(a, c, d), point_segment_distance(b, c, d),
               point_segment_distance(c, a, b), point_segment_distance(d, a, b)) <= radius + EPS:
            return True
    return False


def bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def poly_area(poly):
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def region_area(region):
    return poly_area(region["outline"]) - sum(poly_area(h) for h in region.get("holes", ()))


def polys_overlap(a, b):
    """True when two polygons share area or boundary. Used for keepout intrusion."""
    if any(point_in_poly(p, b) for p in a) or any(point_in_poly(p, a) for p in b):
        return True
    na, nb = len(a), len(b)
    for i in range(na):
        for j in range(nb):
            if _proper_cross(a[i], a[(i + 1) % na], b[j], b[(j + 1) % nb]):
                return True
    return False


def region_overlaps_poly(region, poly):
    """A filled region intrudes into `poly` unless the overlap is entirely inside a hole."""
    if not polys_overlap(region["outline"], poly):
        return False
    for h in region.get("holes", ()):
        if all(point_in_poly(p, h) for p in poly):
            return False
    return True
