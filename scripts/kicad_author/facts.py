"""Board facts: a plain-data snapshot of what is actually on a saved board.

Why a snapshot instead of checking the live `pcbnew` object:

  · **Verify the artifact, not the generator.** The generator's own variables cannot be
    wrong about themselves. Only the file that was saved, reloaded and re-filled can be.
    Extraction therefore reloads the board from disk (see extract.py) and records the
    sha256 of that exact file.
  · **Checks stay testable.** Every predicate in checks.py runs on this dict with the
    standard library, so the check engine is exercised without a KiCad install.
  · **Reports cannot outlive their board.** `verify_binding` refuses a facts file whose
    hash no longer matches the board on disk, which is how a stale DRC result gets
    quoted after an edit.

Units are mm throughout; Y points down, as in the board file.
"""
import copy
import datetime
import json
import pathlib

from .status import bind_file, sha256_of

SCHEMA = 2

EMPTY = {"schema": SCHEMA, "copper_layers": [], "layer_roles": {}, "outline": None,
         "netclasses": {}, "net_class_of": {}, "footprints": [], "pads": [],
         "tracks": [], "vias": [], "zones": [], "rule_areas": [], "nets": []}


def new(board_path, extra=None):
    f = copy.deepcopy(EMPTY)
    f["board"] = bind_file(board_path)
    f["extracted"] = datetime.datetime.now().isoformat(timespec="seconds")
    f.update(extra or {})
    return f


def save(facts, path):
    pathlib.Path(path).write_text(json.dumps(facts, indent=1, sort_keys=False) + "\n")


def load(path):
    f = json.loads(pathlib.Path(path).read_text())
    if f.get("schema") != SCHEMA:
        raise ValueError("facts schema %r, expected %r — run kicad-author facts to re-extract"
                         % (f.get("schema"), SCHEMA))
    return f


def verify_binding(facts, board_path):
    """Return (ok, message). A mismatch means the board changed after extraction."""
    p = pathlib.Path(board_path)
    if not p.exists():
        return False, "board file missing: %s" % p
    have = sha256_of(p)
    want = (facts.get("board") or {}).get("sha256")
    if have != want:
        return False, ("facts describe sha256 %s but %s is now %s — re-extract"
                       % ((want or "?")[:16], p.name, have[:16]))
    return True, "facts bound to %s sha256 %s" % (p.name, have[:16])


def companion_is_fresh(bound, report_path):
    """True when an external report is at least as new as the artifact it describes.

    An older file is not a pass and not a failure: it is unknown, because it describes
    an artifact that no longer exists. Compare each report against **its own** artifact:
    erc.json against the schematic, drc.json against the board. Comparing ERC against the
    board would mark every ERC run stale, since the board is generated after it.
    """
    p = pathlib.Path(report_path)
    if not p.exists():
        return None
    board_mtime = (bound.get("board") or {}).get("mtime_ns")
    if board_mtime is None:
        return None
    return p.stat().st_mtime_ns >= board_mtime


def via_layers(copper_layers, endpoints):
    """Expand a via span in physical stack order, independent of KiCad layer IDs."""
    def order(name):
        return 0 if name == "F.Cu" else 33 if name == "B.Cu" else int(name[2:-3])
    lo, hi = sorted(order(n) for n in (endpoints[0], endpoints[-1]))
    return sorted((n for n in copper_layers if lo <= order(n) <= hi), key=order)


# ── accessors ──────────────────────────────────────────────────────────────────

def pads(facts, ref=None, net=None):
    out = facts.get("pads", [])
    if ref is not None:
        out = [p for p in out if p["ref"] == ref]
    if net is not None:
        out = [p for p in out if same_net(p.get("net"), net)]
    return out


def pad(facts, ref, number):
    for p in facts.get("pads", []):
        if p["ref"] == ref and p["pad"] == str(number):
            return p
    return None


def same_net(a, b):
    """Power nets are global (`GND`); signal nets carry a sheet path (`/CANH`)."""
    if a is None or b is None:
        return False
    return a.lstrip("/") == b.lstrip("/")


def tracks(facts, net=None, layer=None):
    out = facts.get("tracks", [])
    if net is not None:
        out = [t for t in out if same_net(t.get("net"), net)]
    if layer is not None:
        out = [t for t in out if t["layer"] == layer]
    return out


def vias(facts, net=None):
    out = facts.get("vias", [])
    if net is not None:
        out = [v for v in out if same_net(v.get("net"), net)]
    return out


def footprint(facts, ref):
    for f in facts.get("footprints", []):
        if f["ref"] == ref:
            return f
    return None


def plane_regions(facts, net, layer):
    """Filled copper regions of `net` on `layer`, as poly.py regions."""
    out = []
    for z in facts.get("zones", []):
        if not same_net(z.get("net"), net):
            continue
        for r in (z.get("filled") or {}).get(layer, []):
            out.append(r)
    return out


def filled_regions_on(facts, layer):
    out = []
    for z in facts.get("zones", []):
        for r in (z.get("filled") or {}).get(layer, []):
            out.append((z.get("net"), r))
    return out


def rule_area(facts, name):
    for a in facts.get("rule_areas", []):
        if a.get("name") == name:
            return a
    return None


def zones_unfilled(facts):
    """Zones with an outline but no filled polygon on any layer.

    This is what an un-refilled save looks like, and it silently invalidates every
    copper question asked afterwards.
    """
    out = []
    for z in facts.get("zones", []):
        if not any((z.get("filled") or {}).values()):
            out.append(z.get("name") or z.get("net") or "?")
    return out
