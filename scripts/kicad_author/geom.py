"""Geometry: grid snapping, symbol rotation, and pin orientation.

Coordinate conventions, recorded here after observed failures:
  Library Y points up, sheet Y points down ⇒ absolute pin position = (X + rx, Y − ry)
  (rx, ry) is the library position rotated counterclockwise by rot about the origin
"""
import math

GRID = 1.27          # 50mil; snap endpoints to avoid ERC endpoint_off_grid

# Pin angle → outward unit direction on the sheet (Y already flipped)
OUTWARD = {0: (-1.0, 0.0), 90: (0.0, 1.0), 180: (1.0, 0.0), 270: (0.0, -1.0)}


def snap(v, grid=GRID):
    return round(round(v / grid) * grid, 4)


def rot_xy(px, py, rot):
    """Rotate library coordinates counterclockwise by rot degrees about the origin."""
    a = math.radians(rot)
    c, s = math.cos(a), math.sin(a)
    return px * c - py * s, px * s + py * c


def pin_abs(origin_x, origin_y, rot, px, py, ang):
    """Return (absolute_x, absolute_y, absolute_angle)."""
    rx, ry = rot_xy(px, py, rot)
    return snap(origin_x + rx), snap(origin_y - ry), int((ang + rot) % 360)


def stub_end(x, y, ang, length):
    dx, dy = OUTWARD[ang % 360]
    return snap(x + dx * length), snap(y + dy * length)


def bbox_of(points, clearance=0.0):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs) - clearance, min(ys) - clearance,
            max(xs) + clearance, max(ys) + clearance)
