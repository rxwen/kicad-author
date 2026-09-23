#!/usr/bin/env python3
"""Unit tests for kicad_author. Standard library only; run `python3 tests/test_units.py`.

Cover observed failures from the conventions, with counterexamples for avoidance rules.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from kicad_author.geom import GRID, OUTWARD, pin_abs, rot_xy, snap, stub_end
from kicad_author.router import pt_on_seg, route, seg_hits_box, seg_overlaps


class TestGeom(unittest.TestCase):
    def test_snap_is_on_grid(self):
        for v in (0.0, 1.0, 3.7, -2.3, 12.699):
            self.assertAlmostEqual(snap(v), round(v / GRID) * GRID, places=3)

    def test_rot_is_counter_clockwise(self):
        self.assertEqual(tuple(round(c, 6) for c in rot_xy(1.0, 0.0, 90)), (0.0, 1.0))
        self.assertEqual(tuple(round(c, 6) for c in rot_xy(1.0, 0.0, 180)), (-1.0, 0.0))

    def test_pin_abs_flips_y(self):
        """Sheet Y points down, library Y points up: absolute position = (X + rx, Y − ry)."""
        self.assertEqual(pin_abs(12.7, 20.32, 0, 2.54, 5.08, 180), (15.24, 15.24, 180))

    def test_pin_abs_rotates_angle(self):
        x, y, a = pin_abs(12.7, 20.32, 90, 2.54, 0.0, 180)
        self.assertEqual(a, 270)
        self.assertEqual((x, y), (12.7, 17.78))   # After a 90° counterclockwise rotation, the pin is above the symbol

    def test_outward_directions(self):
        self.assertEqual(OUTWARD[0], (-1, 0))     # Pin on the left → lead out to the left
        self.assertEqual(OUTWARD[90], (0, 1))     # Pin below → lead downward (Y already flipped)
        self.assertEqual(OUTWARD[180], (1, 0))
        self.assertEqual(OUTWARD[270], (0, -1))

    def test_stub_end_follows_outward(self):
        self.assertEqual(stub_end(12.7, 12.7, 180, 2.54), (15.24, 12.7))
        self.assertEqual(stub_end(12.7, 12.7, 270, 2.54), (12.7, 10.16))


class TestSegmentPredicates(unittest.TestCase):
    def test_perpendicular_crossing_is_not_overlap(self):
        """Schematic crossings do not imply connection and must not block routing."""
        self.assertFalse(seg_overlaps((0, 5, 10, 5), (5, 0, 5, 10)))

    def test_collinear_overlap_detected(self):
        self.assertTrue(seg_overlaps((0, 5, 10, 5), (4, 5, 12, 5)))

    def test_collinear_touching_at_a_point_is_not_overlap(self):
        self.assertFalse(seg_overlaps((0, 5, 10, 5), (10, 5, 20, 5)))

    def test_pt_on_seg(self):
        self.assertTrue(pt_on_seg(5, 5, 0, 5, 10, 5))
        self.assertFalse(pt_on_seg(5, 6, 0, 5, 10, 5))

    def test_seg_hits_box(self):
        box = (4, 4, 6, 6)
        self.assertTrue(seg_hits_box(0, 5, 10, 5, box))
        self.assertFalse(seg_hits_box(0, 0, 10, 0, box))


class TestRouter(unittest.TestCase):
    ENDS = [(0.0, 0.0), (0.0, 12.7)]

    def test_routes_two_pins(self):
        r = route(self.ENDS, boxes=[])
        self.assertIsNotNone(r)

    def test_avoids_foreign_endpoint(self):
        """Crossing a foreign endpoint silently shorts nets. See §5.1; observed netlist matches fell from 131 to 89."""
        r = route(self.ENDS, boxes=[], foreign_pts=[(0.0, 6.35)])
        self.assertIsNotNone(r)
        for (x1, y1, x2, y2) in r[0]:
            self.assertFalse(pt_on_seg(0.0, 6.35, x1, y1, x2, y2))

    def test_tries_both_orientations(self):
        """Trying only the larger-span orientation can block USB differential pairs. See §5."""
        ends = [(0.0, 0.0), (12.7, 0.0)]
        blocker = (6.35, -50.0, 6.35, 50.0)      # Block all vertical trunks
        r = route(ends, boxes=[], foreign_segs=[blocker])
        self.assertIsNotNone(r)

    def test_single_pin_is_unroutable(self):
        self.assertIsNone(route([(0.0, 0.0)], boxes=[]))

    def test_gives_up_instead_of_shorting(self):
        """Return None when no valid route exists; the caller uses labels. Never return an invalid route."""
        walls = [(x * GRID, -100.0, x * GRID, 100.0) for x in range(-40, 41)]
        self.assertIsNone(route(self.ENDS, boxes=[], foreign_segs=walls))


if __name__ == "__main__":
    unittest.main(verbosity=2)
