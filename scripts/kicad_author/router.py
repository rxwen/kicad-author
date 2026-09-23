"""Orthogonal comb router for schematics.

Topology: one trunk + branches to pin stub endpoints + junctions.

All four avoidance rules are required (each comes from an observed failure; see
references/schematic-layout-conventions.md §5.1 and the incident log in §9):
  1. Do not cross component bodies
  2. Do not cross other nets' endpoints: crossing connects them, silently shorting them
  3. Do not overlap collinear wires from other nets: overlapping wires merge
  4. Perpendicular crossings are allowed: crossing does not imply connection
"""
from .geom import GRID, snap

EPS = 1e-6


def seg_hits_box(x1, y1, x2, y2, box):
    bx0, by0, bx1, by1 = box
    return not (max(x1, x2) < bx0 or min(x1, x2) > bx1
                or max(y1, y2) < by0 or min(y1, y2) > by1)


def pt_on_seg(px, py, x1, y1, x2, y2):
    if abs(x1 - x2) < EPS:                       # Vertical segment
        return abs(px - x1) < EPS and min(y1, y2) - EPS <= py <= max(y1, y2) + EPS
    if abs(y1 - y2) < EPS:                       # Horizontal segment
        return abs(py - y1) < EPS and min(x1, x2) - EPS <= px <= max(x1, x2) + EPS
    return False


def seg_overlaps(a, b):
    """Check collinear overlap of orthogonal segments; perpendicular crossings return False."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    a_vert, b_vert = abs(ax1 - ax2) < EPS, abs(bx1 - bx2) < EPS
    if a_vert != b_vert:
        return False
    if a_vert:
        return (abs(ax1 - bx1) < EPS
                and min(ay1, ay2) < max(by1, by2) - EPS
                and max(ay1, ay2) > min(by1, by2) + EPS)
    return (abs(ay1 - by1) < EPS
            and min(ax1, ax2) < max(bx1, bx2) - EPS
            and max(ax1, ax2) > min(bx1, bx2) + EPS)


def _trunk_candidates(base):
    """Trunk candidates: at endpoints → between endpoints → farther out on both sides.

    Initially, positions between endpoints were omitted, preventing USB differential pair routing.
    """
    lo, hi = snap(min(base)), snap(max(base))
    cands = [snap(v) for v in sorted(set(base))]
    n_mid = int(round((hi - lo) / GRID))
    cands += [snap(lo + k * GRID) for k in range(1, max(n_mid, 1))]
    cands += [snap(lo - k * GRID) for k in range(1, 14)]
    cands += [snap(hi + k * GRID) for k in range(1, 14)]
    seen, uniq = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def _try_axis(ends, vertical, boxes, foreign_pts, foreign_segs):
    xs = [p[0] for p in ends]
    ys = [p[1] for p in ends]
    if vertical:
        lo, hi, base = min(ys), max(ys), xs
    else:
        lo, hi, base = min(xs), max(xs), ys
    if hi - lo < EPS:
        return None
    for T in _trunk_candidates(base):
        segs = []
        for (sx, sy) in ends:
            if vertical and abs(sx - T) > EPS:
                segs.append((sx, sy, T, sy))
            elif not vertical and abs(sy - T) > EPS:
                segs.append((sx, sy, sx, T))
        segs.append((T, lo, T, hi) if vertical else (lo, T, hi, T))
        if any(seg_hits_box(*s, bx) for s in segs for bx in boxes):
            continue
        if any(pt_on_seg(fx, fy, *s) for s in segs for (fx, fy) in foreign_pts):
            continue
        if any(seg_overlaps(s, fs) for s in segs for fs in foreign_segs):
            continue
        junc = []
        for (sx, sy) in ends:
            v = sy if vertical else sx
            if abs(v - lo) > EPS and abs(v - hi) > EPS:
                junc.append((T, sy) if vertical else (sx, T))
        return segs, junc
    return None


def route(ends, boxes, foreign_pts=(), foreign_segs=()):
    """Return (segments, junctions), or None if no route exists; the caller falls back to labels.

    Try both orientations; choosing only the larger span can block USB differential pairs.
    """
    if len(ends) < 2:
        return None
    xs = [p[0] for p in ends]
    ys = [p[1] for p in ends]
    prefer_vertical = (max(ys) - min(ys)) >= (max(xs) - min(xs))
    for vertical in (prefer_vertical, not prefer_vertical):
        r = _try_axis(ends, vertical, boxes, foreign_pts, foreign_segs)
        if r:
            return r
    return None
