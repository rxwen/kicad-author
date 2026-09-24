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




# ── four-state results and tiering ─────────────────────────────────────────────

from kicad_author import checks as chk          # noqa: E402
from kicad_author import facts as F             # noqa: E402
from kicad_author import poly                   # noqa: E402
from kicad_author import relations as R         # noqa: E402
from kicad_author import stack as S             # noqa: E402
from kicad_author.status import (FAIL, NA, PASS, UNKNOWN,   # noqa: E402
                                 Check, Report, bind_file)


class TestStatus(unittest.TestCase):
    def test_hard_tier_failure_gates(self):
        self.assertTrue(Check("a", 1, "t", FAIL).gates())
        self.assertTrue(Check("a", 2, "t", FAIL).gates())

    def test_optimisation_tier_failure_never_gates(self):
        """Tier 3-4 are reported, so a tidier board can never offset a broken one."""
        self.assertFalse(Check("a", 3, "t", FAIL).gates())
        self.assertFalse(Check("a", 4, "t", FAIL).gates())

    def test_unknown_gates_only_when_declared_critical(self):
        self.assertFalse(Check("a", 2, "t", UNKNOWN).gates())
        self.assertTrue(Check("a", 2, "t", UNKNOWN, critical=True).gates())

    def test_waiver_is_the_only_way_past_a_gating_unknown(self):
        r = Report(waivers={"a": "measured externally"})
        c = r.add(Check("a", 2, "t", UNKNOWN, critical=True))
        self.assertFalse(c.gates())
        self.assertEqual(r.exit_code(), 0)
        self.assertIn("measured externally", r.text())

    def test_no_aggregate_score_exists(self):
        """There is no total to trade with: only counts per state and a gating list."""
        r = Report()
        r.add_many([Check("a", 1, "t", FAIL), Check("b", 4, "t", PASS)])
        self.assertEqual(r.counts(), {PASS: 1, FAIL: 1, UNKNOWN: 0, NA: 0})
        self.assertEqual([c.id for c in r.gating()], ["a"])
        self.assertNotIn("score", r.as_dict())

    def test_stale_waiver_is_reported(self):
        r = Report(waivers={"gone": "reason"})
        r.add(Check("a", 1, "t", PASS))
        self.assertEqual(r.unused_waivers(), ["gone"])


class TestPoly(unittest.TestCase):
    REGION = {"outline": [(0, 0), (10, 0), (10, 10), (0, 10)],
              "holes": [[(4, 0), (6, 0), (6, 10), (4, 10)]]}

    def test_hole_is_not_inside(self):
        self.assertTrue(poly.point_in_region((1, 1), self.REGION))
        self.assertFalse(poly.point_in_region((5, 5), self.REGION))

    def test_segment_crossing_a_plane_split_is_not_backed(self):
        """The filled polygon carries the split, so a broken reference shows up here."""
        self.assertFalse(poly.seg_inside_region((1, 5), (9, 5), self.REGION))
        self.assertTrue(poly.seg_inside_region((1, 1), (3, 9), self.REGION))

    def test_segment_spanning_two_regions_is_backed_by_neither(self):
        a = {"outline": [(0, 0), (4, 0), (4, 4), (0, 4)], "holes": []}
        b = {"outline": [(6, 0), (10, 0), (10, 4), (6, 4)], "holes": []}
        self.assertFalse(poly.seg_inside_any((1, 2), (9, 2), [a, b]))

    def test_bounding_box_trap(self):
        """An L-shaped pour clears a corner its bounding box would claim to occupy."""
        L = [(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)]
        self.assertFalse(poly.point_in_poly((8, 8), L))
        self.assertTrue(poly.polys_overlap(L, [(1, 1), (2, 1), (2, 2), (1, 2)]))
        self.assertFalse(poly.polys_overlap(L, [(7, 7), (8, 7), (8, 8), (7, 8)]))


# ── facts and binding ──────────────────────────────────────────────────────────

def make_facts(**kw):
    f = dict(F.EMPTY)
    f.update({"board": {"path": "b.kicad_pcb", "sha256": "x", "bytes": 1,
                        "mtime": "2026-01-01T00:00:00"},
              "copper_layers": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"],
              "outline": [0, 0, 50, 40], "footprints": [], "pads": [], "tracks": [],
              "vias": [], "zones": [], "rule_areas": [], "netclasses": {},
              "net_class_of": {}})
    f.update(kw)
    return f


def pad(ref, num, net, x, y, layers=("F.Cu",)):
    return {"ref": ref, "pad": str(num), "net": net, "pos": [x, y],
            "layers": list(layers), "type": "smd", "size": [0.5, 0.5]}


class TestFactsBinding(unittest.TestCase):
    def test_binding_detects_an_edited_board(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".kicad_pcb", delete=False) as fh:
            fh.write("v1")
            p = pathlib.Path(fh.name)
        facts = {"board": bind_file(p)}
        self.assertTrue(F.verify_binding(facts, p)[0])
        p.write_text("v2")
        ok, msg = F.verify_binding(facts, p)
        self.assertFalse(ok)
        self.assertIn("re-extract", msg)
        p.unlink()

    def test_unfilled_pours_are_visible(self):
        f = make_facts(zones=[{"name": "gnd", "net": "GND", "layers": ["In1.Cu"],
                               "outline": [], "filled": {}}])
        self.assertEqual(F.zones_unfilled(f), ["gnd"])
        c = [c for c in chk.artifact(f) if c.id == "t1.zones-filled"][0]
        self.assertEqual(c.status, FAIL)
        self.assertTrue(c.gates())

    def test_sheet_path_prefix_does_not_break_net_matching(self):
        self.assertTrue(F.same_net("/CANH", "CANH"))


# ── relations ──────────────────────────────────────────────────────────────────

class FakeDesign:
    def __init__(self, groups):
        self.PARTS = {r: (r, "", "", "", "") for r in ("U1", "C1", "U2", "J2", "D2")}
        self.NETS = {"+3V3": [("U1", "1"), ("C1", "1")],
                     "GND": [("U1", "2"), ("C1", "2"), ("D2", "2")],
                     "CANH": [("J2", "1"), ("U2", "7"), ("D2", "1")]}
        self.GROUPS = groups


DEC = {"u1": ("decouple", {"cap": "C1", "ic": "U1", "pin": "1", "net": "+3V3",
                           "ret": "GND", "max_mm": 2.0, "ret_max_mm": 3.0})}
TVS = {"can": ("protect", {"device": "D2", "net": "CANH",
                           "exposed": ("J2", "1"), "protected": ("U2", "7")})}


class TestRelations(unittest.TestCase):
    def test_wrong_supply_pin_is_a_declaration_error(self):
        """The group must name the pin it actually serves, checked against NETS."""
        d = FakeDesign({"u1": ("decouple", {"cap": "C1", "ic": "U1", "pin": "2",
                                            "net": "+3V3"})})
        self.assertTrue(any("not the declared" in e for e in R.validate(d)))

    def test_protection_device_must_be_on_the_net_it_protects(self):
        d = FakeDesign({"can": ("protect", {"device": "C1", "net": "CANH",
                                            "exposed": ("J2", "1"),
                                            "protected": ("U2", "7")})})
        self.assertTrue(any("is not on net CANH" in e for e in R.validate(d)))

    def test_decoupling_expands_to_pin_orientation_and_return(self):
        kinds = {c["id"].split(".")[-1] for c in R.expand(FakeDesign(DEC))}
        self.assertEqual(kinds, {"serves", "facing", "return"})

    def test_capacitor_serving_the_declared_pin_passes(self):
        f = make_facts(pads=[pad("U1", 1, "+3V3", 24, 18), pad("U1", 2, "GND", 24, 19),
                             pad("C1", 1, "+3V3", 25, 18), pad("C1", 2, "GND", 25.8, 18)])
        got = {c.id: c for c in chk.relations(f, FakeDesign(DEC))}
        self.assertEqual(got["t2.rel.u1.serves"].status, PASS)
        self.assertEqual(got["t2.rel.u1.facing"].status, PASS)

    def test_reversed_capacitor_fails_on_orientation_alone(self):
        """Centre distance passes; the pad relationship is what catches the rotation."""
        f = make_facts(pads=[pad("U1", 1, "+3V3", 24, 18), pad("U1", 2, "GND", 24, 19),
                             pad("C1", 1, "+3V3", 25.8, 18), pad("C1", 2, "GND", 25, 18)])
        got = {c.id: c for c in chk.relations(f, FakeDesign(DEC))}
        self.assertEqual(got["t2.rel.u1.serves"].status, PASS)
        self.assertEqual(got["t2.rel.u1.facing"].status, FAIL)
        self.assertIn("wrong way round", got["t2.rel.u1.facing"].detail)

    def test_return_path_with_nothing_to_return_to_is_unknown(self):
        f = make_facts(pads=[pad("U1", 1, "+3V3", 24, 18),
                             pad("C1", 1, "+3V3", 25, 18), pad("C1", 2, "GND", 25.8, 18)])
        got = {c.id: c for c in chk.relations(f, FakeDesign(DEC))}
        self.assertEqual(got["t2.rel.u1.return"].status, UNKNOWN)
        self.assertTrue(got["t2.rel.u1.return"].gates())

    def test_tvs_beside_the_victim_fails_even_though_it_is_nearer_the_connector(self):
        """30 mm from J2 and 2 mm from U2 is not protection, however the distances rank."""
        f = make_facts(pads=[pad("J2", 1, "CANH", 6, 20), pad("U2", 7, "CANH", 38, 20),
                             pad("D2", 1, "CANH", 36, 20), pad("D2", 2, "GND", 36, 21)])
        got = {c.id: c for c in chk.relations(f, FakeDesign(TVS))}
        self.assertEqual(got["t2.rel.can.between"].status, FAIL)

    def test_tvs_at_the_connector_passes(self):
        f = make_facts(pads=[pad("J2", 1, "CANH", 6, 20), pad("U2", 7, "CANH", 38, 20),
                             pad("D2", 1, "CANH", 8, 20), pad("D2", 2, "GND", 8, 21)])
        got = {c.id: c for c in chk.relations(f, FakeDesign(TVS))}
        self.assertEqual(got["t2.rel.can.between"].status, PASS)

    def test_no_groups_declared_is_unknown_not_pass(self):
        got = chk.relations(make_facts(), FakeDesign({}))
        self.assertEqual([c.status for c in got], [UNKNOWN])


# ── layers, planes and rules ───────────────────────────────────────────────────

class FakePhysical:
    COPPER_LAYERS = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    LAYER_ROLE = {"F.Cu": "signal", "In1.Cu": "plane", "In2.Cu": "plane", "B.Cu": "signal"}
    PLANES = {"In1.Cu": "GND", "In2.Cu": "+3V3"}
    NETCLASSES = {"Default": {"track_width": 0.2}, "CAN": {"track_width": 0.25}}
    NET_CLASS = {"CANH": "CAN"}
    CRITICAL_NETS = {"CANH": {"reference": "In1.Cu", "return": "GND", "max_vias": 1,
                              "stitch_mm": 3.0, "impedance": 60}}
    KEEPOUTS = {"antenna": {"rect": (40.0, 0.0, 50.0, 12.0), "layers": "all"}}
    DOMAINS = {"main": {"nets": ["GND", "CANH"]}}


class TwoDomains(FakePhysical):
    DOMAINS = {"main": {"nets": ["GND", "CANH"]}, "iso": {"nets": ["VISO", "CAN_GND"]}}
    DOMAIN_CLEARANCE = 4.0


PLANE_FULL = {"outline": [[0, 0], [50, 0], [50, 40], [0, 40]], "holes": []}
PLANE_SPLIT = {"outline": [[0, 0], [50, 0], [50, 40], [0, 40]],
               "holes": [[[20, 10], [21, 10], [21, 30], [20, 30]]]}


def plane_facts(filled, **kw):
    f = make_facts(zones=[{"name": "p1", "net": "GND", "layers": ["In1.Cu"],
                           "outline": PLANE_FULL["outline"], "filled": {"In1.Cu": [filled]}}],
                   netclasses={"Default": {"track": 0.2}, "CAN": {"track": 0.25}},
                   net_class_of={"CANH": "CAN"}, **kw)
    return f


class TestStack(unittest.TestCase):
    def test_plane_on_a_signal_layer_is_a_declaration_error(self):
        class P(FakePhysical):
            LAYER_ROLE = dict(FakePhysical.LAYER_ROLE, **{"In1.Cu": "signal"})
        errs = S.validate(P, FakeDesign({}))
        self.assertTrue(any("must have role 'plane'" in e for e in errs))

    def test_critical_net_must_reference_a_real_plane(self):
        class P(FakePhysical):
            CRITICAL_NETS = {"CANH": {"reference": "B.Cu"}}
        self.assertTrue(any("carries no plane" in e for e in S.validate(P, FakeDesign({}))))

    def test_keepout_naming_a_layer_outside_the_stack_is_rejected(self):
        class P(FakePhysical):
            KEEPOUTS = {"antenna": {"rect": (0, 0, 1, 1), "layers": ["In5.Cu"]}}
        self.assertTrue(any("not in the stack" in e for e in S.validate(P, FakeDesign({}))))

    def test_missing_physical_declaration_is_reported_not_assumed(self):
        self.assertTrue(S.validate(None, FakeDesign({})))

    def test_domain_clearance_becomes_a_custom_rule(self):
        rules = "\n".join(S.dru_rules(TwoDomains))
        self.assertIn("isolation", rules)
        self.assertIn("4.0mm", rules)

    def test_netclass_settings_carry_defaults_and_patterns(self):
        ns = S.netclass_settings(FakePhysical)
        self.assertEqual(ns["classes"][0]["name"], "Default")
        self.assertEqual(ns["netclass_patterns"], [{"netclass": "CAN", "pattern": "CANH"}])


class TestPlaneChecks(unittest.TestCase):
    def test_track_over_an_intact_plane_passes(self):
        f = plane_facts(PLANE_FULL, tracks=[{"net": "CANH", "layer": "F.Cu",
                                             "start": [6, 20], "end": [18, 20], "width": 0.25}])
        got = {c.id: c for c in chk.layers_and_planes(f, FakePhysical, FakeDesign({}))}
        self.assertEqual(got["t2.plane.CANH"].status, PASS)

    def test_track_crossing_a_plane_split_fails(self):
        f = plane_facts(PLANE_SPLIT, tracks=[{"net": "CANH", "layer": "F.Cu",
                                              "start": [18, 20], "end": [24, 20], "width": 0.25}])
        got = {c.id: c for c in chk.layers_and_planes(f, FakePhysical, FakeDesign({}))}
        self.assertEqual(got["t2.plane.CANH"].status, FAIL)
        self.assertTrue(got["t2.plane.CANH"].gates())

    def test_unrouted_critical_net_is_not_applicable_not_passing(self):
        got = {c.id: c for c in chk.layers_and_planes(plane_facts(PLANE_FULL),
                                                      FakePhysical, FakeDesign({}))}
        self.assertEqual(got["t2.plane.CANH"].status, NA)

    def test_layer_change_without_a_return_via_fails(self):
        f = plane_facts(PLANE_FULL,
                        vias=[{"net": "CANH", "pos": [24, 20], "diameter": 0.6,
                               "drill": 0.3, "layers": ["F.Cu", "B.Cu"]}])
        got = {c.id: c for c in chk.layers_and_planes(f, FakePhysical, FakeDesign({}))}
        self.assertEqual(got["t2.return.CANH"].status, FAIL)

    def test_return_via_inside_a_rule_area_does_not_count(self):
        """Stitching is never blanket: a via inside a keepout or barrier is not a return."""
        f = plane_facts(PLANE_FULL,
                        vias=[{"net": "CANH", "pos": [42, 6], "diameter": 0.6, "drill": 0.3,
                               "layers": ["F.Cu", "B.Cu"]},
                              {"net": "GND", "pos": [43, 6], "diameter": 0.6, "drill": 0.3,
                               "layers": ["F.Cu", "B.Cu"]}],
                        rule_areas=[{"name": "antenna", "layers": FakePhysical.COPPER_LAYERS,
                                     "outline": [[40, 0], [50, 0], [50, 12], [40, 12]]}])
        got = {c.id: c for c in chk.layers_and_planes(f, FakePhysical, FakeDesign({}))}
        self.assertEqual(got["t2.return.CANH"].status, FAIL)

    def test_via_budget_is_enforced(self):
        f = plane_facts(PLANE_FULL,
                        vias=[{"net": "CANH", "pos": [24, 20], "diameter": 0.6, "drill": 0.3,
                               "layers": ["F.Cu", "B.Cu"]},
                              {"net": "CANH", "pos": [30, 20], "diameter": 0.6, "drill": 0.3,
                               "layers": ["F.Cu", "B.Cu"]}])
        got = {c.id: c for c in chk.layers_and_planes(f, FakePhysical, FakeDesign({}))}
        self.assertEqual(got["t2.plane.CANH.vias"].status, FAIL)

    def test_impedance_is_never_a_pass_without_a_stackup(self):
        got = {c.id: c for c in chk.layers_and_planes(plane_facts(PLANE_FULL),
                                                      FakePhysical, FakeDesign({}))}
        c = got["t2.impedance.CANH"]
        self.assertEqual(c.status, UNKNOWN)
        self.assertTrue(c.gates())

    def test_keepout_must_exist_on_the_board(self):
        got = {c.id: c for c in chk.layers_and_planes(plane_facts(PLANE_FULL),
                                                      FakePhysical, FakeDesign({}))}
        self.assertEqual(got["t1.keepout.antenna"].status, FAIL)

    def test_keepout_covering_only_the_top_layer_fails(self):
        """A blank rectangle on F.Cu is not an antenna clearance on a four-layer board."""
        f = plane_facts(PLANE_FULL,
                        rule_areas=[{"name": "antenna", "layers": ["F.Cu"],
                                     "outline": [[40, 0], [50, 0], [50, 12], [40, 12]]}])
        got = {c.id: c for c in chk.layers_and_planes(f, FakePhysical, FakeDesign({}))}
        self.assertEqual(got["t1.keepout.antenna"].status, FAIL)
        self.assertIn("layers the rule area does not reach", got["t1.keepout.antenna"].detail)

    def test_copper_inside_a_correct_keepout_is_caught(self):
        f = plane_facts(PLANE_FULL,
                        tracks=[{"net": "+3V3", "layer": "F.Cu", "start": [42, 4],
                                 "end": [48, 4], "width": 0.5}],
                        rule_areas=[{"name": "antenna", "layers": FakePhysical.COPPER_LAYERS,
                                     "outline": [[40, 0], [50, 0], [50, 12], [40, 12]]}])
        got = {c.id: c for c in chk.layers_and_planes(f, FakePhysical, FakeDesign({}))}
        self.assertEqual(got["t1.keepout.antenna"].status, FAIL)

    def test_netclass_not_in_force_on_the_board_fails(self):
        f = plane_facts(PLANE_FULL)
        f["net_class_of"] = {"CANH": "Default"}
        got = {c.id: c for c in chk.layers_and_planes(f, FakePhysical, FakeDesign({}))}
        self.assertEqual(got["t1.netclasses"].status, FAIL)

    def test_stack_mismatch_is_caught(self):
        f = plane_facts(PLANE_FULL)
        f["copper_layers"] = ["F.Cu", "B.Cu"]
        got = {c.id: c for c in chk.layers_and_planes(f, FakePhysical, FakeDesign({}))}
        self.assertEqual(got["t1.stack-matches"].status, FAIL)


    def test_plane_cuts_are_reported_but_do_not_gate(self):
        f = plane_facts(PLANE_FULL, tracks=[{"net": "GND", "layer": "In1.Cu",
                                             "start": [2, 2], "end": [8, 2], "width": 0.3}])
        c = {c.id: c for c in chk.layers_and_planes(f, FakePhysical, FakeDesign({}))}["t3.plane-cuts"]
        self.assertEqual(c.status, FAIL)
        self.assertFalse(c.gates())


class TestCrossDomainReference(unittest.TestCase):
    def test_reference_plane_in_another_domain_is_a_declaration_error(self):
        """No placement or routing can fix this, so it fails at tier 1, not on the board.
        Stitching a net to a plane in another domain would bridge the barrier."""
        class P(FakePhysical):
            DOMAINS = {"main": {"nets": ["GND"]}, "iso": {"nets": ["CANH"]}}
        errs = S.validate(P, FakeDesign({}))
        self.assertTrue(any("which is in domain main" in e for e in errs), errs)

    def test_same_domain_reference_is_accepted(self):
        self.assertEqual(S.validate(FakePhysical, FakeDesign({})), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
