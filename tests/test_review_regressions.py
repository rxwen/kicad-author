"""Regression cases for copper extents, via spans and missing/stale evidence."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from kicad_author import checks, facts, poly, status


class CopperIntrusions(unittest.TestCase):
    region = [(10, 0), (12, 0), (12, 10), (10, 10)]
    layers = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]

    def test_track_width_and_round_end(self):
        for a, b in [([9.9, 1], [9.9, 9]), ([8, 5], [9.9, 5])]:
            f = {"tracks": [{"net": "GND", "layer": "F.Cu", "start": a,
                              "end": b, "width": 0.4}]}
            self.assertTrue(checks._intrusions(f, self.region, ["F.Cu"]))
        self.assertFalse(poly.stroke_touches_poly((9.7, 1), (9.7, 9), 0.2, self.region))

    def test_via_diameter_and_inner_layer_span(self):
        f = {"copper_layers": self.layers, "vias": [
            {"net": "GND", "pos": [9.9, 5], "diameter": 0.6,
             "layers": ["F.Cu", "B.Cu"]}]}
        self.assertTrue(checks._intrusions(f, self.region, ["In1.Cu"]))
        f["vias"][0]["layers"] = ["F.Cu", "In1.Cu"]
        self.assertFalse(checks._intrusions(f, self.region, ["In2.Cu"]))
        f["vias"][0]["pos"] = [9.6, 5]
        self.assertFalse(checks._intrusions(f, self.region, ["In1.Cu"]))

    def test_span_uses_physical_order_not_numeric_layer_id_order(self):
        stack = ["F.Cu", "B.Cu", "In1.Cu", "In2.Cu"]
        self.assertEqual(facts.via_layers(stack, ["F.Cu", "B.Cu"]), self.layers)
        self.assertEqual(facts.via_layers(stack, ["In2.Cu", "In1.Cu"]),
                         ["In1.Cu", "In2.Cu"])

    def test_pad_extent_uses_transformed_copper_not_origin(self):
        pad = {"ref": "U1", "pad": "1", "pos": [9, 5], "layers": ["F.Cu"],
               "copper": {"F.Cu": [{"outline": [[8, 5], [9, 4], [10.1, 5], [9, 6]],
                                      "holes": []}]}}
        self.assertTrue(checks._intrusions({"pads": [pad]}, self.region, ["F.Cu"]))
        pad["copper"]["F.Cu"][0]["outline"][2] = [9.9, 5]
        self.assertFalse(checks._intrusions({"pads": [pad]}, self.region, ["F.Cu"]))

    def test_missing_pad_geometry_cannot_pass(self):
        pad = {"ref": "U1", "pad": "1", "pos": [9, 5], "layers": ["F.Cu"]}
        self.assertTrue(checks._intrusions({"pads": [pad]}, self.region, ["F.Cu"]))


class ClassEvidence(unittest.TestCase):
    physical = SimpleNamespace(NETCLASSES={"Power": {"track_width": 0.5}},
                               NET_CLASS={"VCC": "Power"})

    def test_missing_assignment_is_gating_unknown(self):
        f = {"netclasses": {"Power": {"track": 0.5}}, "net_class_of": {}}
        c = checks._netclass_checks(f, self.physical)[0]
        self.assertEqual(c.status, status.UNKNOWN)
        self.assertTrue(c.gates())

    def test_missing_class_property_is_gating_unknown(self):
        f = {"netclasses": {"Power": {}}, "net_class_of": {"VCC": "Power"}}
        c = checks._netclass_checks(f, self.physical)[0]
        self.assertEqual(c.status, status.UNKNOWN)
        self.assertTrue(c.gates())

    def test_complete_assignment_passes_and_mismatch_fails(self):
        f = {"netclasses": {"Power": {"track": 0.5}}, "net_class_of": {"/VCC": "Power"}}
        self.assertEqual(checks._netclass_checks(f, self.physical)[0].status, status.PASS)
        f["net_class_of"]["/VCC"] = "Default"
        self.assertEqual(checks._netclass_checks(f, self.physical)[0].status, status.FAIL)


class ReportFreshness(unittest.TestCase):
    def test_legacy_snapshot_requires_reextraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'facts.json'
            path.write_text('{"schema": 1}')
            with self.assertRaisesRegex(ValueError, 're-extract'):
                facts.load(path)

    def test_subsecond_stale_report_gates_and_new_report_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            board, report = pathlib.Path(tmp) / "board", pathlib.Path(tmp) / "drc.json"
            board.write_text("new board")
            report.write_text(json.dumps({"violations": [], "unconnected_items": [],
                                          "schematic_parity": []}))
            base = 1700000000 * 10**9
            os.utime(board, ns=(base + 900000000, base + 900000000))
            os.utime(report, ns=(base + 100000000, base + 100000000))
            bound = {"board": status.bind_file(board)}
            self.assertFalse(facts.companion_is_fresh(bound, report))
            self.assertTrue(any(c.gates() for c in checks.drc_checks(bound, report)))
            os.utime(report, ns=(base + 950000000, base + 950000000))
            self.assertTrue(facts.companion_is_fresh(bound, report))
            self.assertFalse(any(c.gates() for c in checks.drc_checks(bound, report)))
            self.assertTrue(any(c.gates() for c in checks.drc_checks({}, report)))

    def test_failed_cli_does_not_reuse_existing_report(self):
        for stage in ("erc", "drc"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                project = pathlib.Path(tmp)
                (project / "src").mkdir()
                (project / "build").mkdir()
                (project / "src/design.py").write_text("BOARD_NAME = 'test'\n")
                (project / "src/build.sh").write_text((ROOT / "templates/build.sh").read_text())
                (project / "build/erc.json").write_text('{}')
                (project / "build/drc.json").write_text('{}')
                (project / "scripts").mkdir()
                ka = project / "scripts/kicad-author"
                ka.write_text('#!/bin/sh\nif [ "$1" = env ]; then\n'
                              '  echo "export KICAD_CLI=$FAKE_CLI KICAD_PY=/bin/true"\nfi\n')
                cli = project / "cli"
                cli.write_text('#!/bin/sh\n[ "$2" != "$FAIL_STAGE" ] || exit 7\nexit 0\n')
                ka.chmod(0o755)
                cli.chmod(0o755)
                env = dict(os.environ, KICAD_AUTHOR_HOME=tmp, FAKE_CLI=str(cli), FAIL_STAGE=stage)
                result = subprocess.run(['bash', str(project / 'src/build.sh'), '--no-route'],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
                self.assertNotIn('Build passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
