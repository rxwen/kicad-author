"""Checks over board facts. Standard library only; no pcbnew.

Every check answers one question about the **saved** board and returns one of four
states. Grouping is by tier (see status.py): tiers 1-2 are hard constraints that gate,
tiers 3-4 are reported and never aggregated into a score.

Artifact-level checks live here (this module, `artifact`); relation checks that consume
declared electrical groups live in `relcheck`; layer and reference-plane checks live in
`plane`. All three take the same facts dict.
"""
import json
import pathlib

from . import facts as F
from . import poly
from .status import FAIL, NA, PASS, UNKNOWN, Check


def _load_json(path):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except Exception:
        return None


def _violations(doc):
    """Collect violations from a kicad-cli drc/erc JSON, whatever the nesting."""
    out = list(doc.get("violations") or [])
    for sheet in doc.get("sheets") or []:
        out += list(sheet.get("violations") or [])
    return out


def _by_severity(items):
    sev = {}
    for v in items:
        sev.setdefault(v.get("severity", "unknown"), []).append(v)
    return sev


def _describe(v, limit=90):
    d = v.get("description") or v.get("type") or "?"
    return d[:limit]


def _external_report(bound, path, id_prefix, title, tier=1, critical=True):
    """Turn a kicad-cli report into checks, refusing to read a stale one as a pass.

    `bound` carries the artifact the report describes, under key "board": the board for
    DRC, the schematic for ERC.
    """
    p = pathlib.Path(path)
    if not p.exists():
        return [Check("%s.missing" % id_prefix, tier, title, UNKNOWN,
                      "%s not found — check never ran" % p.name, critical=critical)]
    fresh = F.companion_is_fresh(bound, p)
    if fresh is not True:
        return [Check("%s.stale" % id_prefix, tier, title, UNKNOWN,
                      "%s is stale or lacks a precise artifact timestamp — rerun the check"
                      % p.name, critical=critical)]
    doc = _load_json(p)
    if doc is None:
        return [Check("%s.unreadable" % id_prefix, tier, title, UNKNOWN,
                      "%s is not valid JSON" % p.name, critical=critical)]
    return doc


def drc_checks(facts, drc_json):
    """Errors gate; warnings are reported at tier 3 instead of being dropped.

    The pipeline previously ran DRC with --severity-error only, so every warning
    (courtyard overlap, clipped silkscreen) was discarded before anyone could read it.
    """
    doc = _external_report(facts, drc_json, "t1.drc", "DRC")
    if isinstance(doc, list):
        return doc
    out = []
    sev = _by_severity(_violations(doc))
    errors, warnings = sev.get("error", []), sev.get("warning", [])
    out.append(Check("t1.drc", 1, "DRC errors", FAIL if errors else PASS,
                     "%d DRC errors" % len(errors) if errors else "no DRC errors",
                     [_describe(v) for v in errors], critical=True))
    out.append(Check("t3.drc-warnings", 3, "DRC warnings",
                     FAIL if warnings else PASS,
                     "%d DRC warnings" % len(warnings) if warnings
                     else "no DRC warnings",
                     [_describe(v) for v in warnings]))
    unconn = doc.get("unconnected_items") or []
    out.append(Check("t1.unconnected", 1, "Unconnected items",
                     FAIL if unconn else PASS,
                     "%d unconnected items" % len(unconn) if unconn
                     else "every net routed", [_describe(v) for v in unconn],
                     critical=True))
    parity = doc.get("schematic_parity")
    if parity is None:
        out.append(Check("t1.parity", 1, "Schematic parity", UNKNOWN,
                         "DRC ran without --schematic-parity", critical=True))
    else:
        out.append(Check("t1.parity", 1, "Schematic parity",
                         FAIL if parity else PASS,
                         "%d parity violations" % len(parity) if parity
                         else "PCB matches the schematic",
                         [_describe(v) for v in parity], critical=True))
    return out


def erc_checks(sch_bound, erc_json):
    """`sch_bound` must be bound to the **schematic**, not the board."""
    doc = _external_report(sch_bound, erc_json, "t1.erc", "ERC")
    if isinstance(doc, list):
        return doc
    sev = _by_severity(_violations(doc))
    errors, warnings = sev.get("error", []), sev.get("warning", [])
    return [
        Check("t1.erc", 1, "ERC errors", FAIL if errors else PASS,
              "%d ERC errors" % len(errors) if errors else "no ERC errors",
              [_describe(v) for v in errors], critical=True),
        Check("t3.erc-warnings", 3, "ERC warnings", FAIL if warnings else PASS,
              "%d ERC warnings" % len(warnings) if warnings else "no ERC warnings",
              [_describe(v) for v in warnings]),
    ]


def artifact(facts, design=None):
    """Checks on the saved board itself: fills, placement, pad nets, mechanical bounds."""
    out = []

    unfilled = F.zones_unfilled(facts)
    if not facts.get("zones"):
        out.append(Check("t1.zones-filled", 1, "Copper pours filled", NA,
                         "board declares no copper pours"))
    elif unfilled:
        out.append(Check("t1.zones-filled", 1, "Copper pours filled", FAIL,
                         "%d pours have no filled polygon — the saved board was never "
                         "re-filled, so every copper answer below is unreliable"
                         % len(unfilled), unfilled, critical=True))
    else:
        out.append(Check("t1.zones-filled", 1, "Copper pours filled", PASS,
                         "%d pours filled" % len(facts["zones"])))

    if design is not None:
        parts = set(getattr(design, "PARTS", {}))
        placed = {f["ref"] for f in facts.get("footprints", [])}
        missing, extra = sorted(parts - placed), sorted(placed - parts)
        out.append(Check("t1.parts-placed", 1, "Every part on the board",
                         FAIL if (missing or extra) else PASS,
                         "missing %s / unexpected %s" % (missing or "none", extra or "none")
                         if (missing or extra) else "%d parts placed" % len(placed),
                         critical=True))
        out += [_pad_nets(facts, design)]

    if facts.get("outline"):
        x0, y0, x1, y1 = facts["outline"]
        outside = [f["ref"] for f in facts.get("footprints", [])
                   if not (x0 - 0.01 <= f["pos"][0] <= x1 + 0.01
                           and y0 - 0.01 <= f["pos"][1] <= y1 + 0.01)]
        out.append(Check("t1.mech-bounds", 1, "Parts inside the board outline",
                         FAIL if outside else PASS,
                         "%d part origins outside the outline" % len(outside)
                         if outside else "all part origins inside %.1f×%.1f mm"
                         % (x1 - x0, y1 - y0), outside, critical=True))
    else:
        out.append(Check("t1.mech-bounds", 1, "Parts inside the board outline", UNKNOWN,
                         "no Edge.Cuts outline on the board", critical=True))

    out.append(Check("t3.routing-volume", 3, "Routing volume",
                     PASS, "%d track segments, %d vias"
                     % (len(facts.get("tracks", [])), len(facts.get("vias", [])))))
    return out


def _pad_nets(facts, design):
    """Compare pad nets on the board against design.NETS, independent of KiCad parity.

    Parity trusts the netlist that KiCad itself exported. This reads the pads.
    """
    want = {}
    for net, pins in getattr(design, "NETS", {}).items():
        for ref, pin in pins:
            want[(ref, str(pin))] = net
    bad, unassigned = [], []
    for (ref, pin), net in sorted(want.items()):
        p = F.pad(facts, ref, pin)
        if p is None:
            bad.append("%s.%s absent from the board" % (ref, pin))
        elif p.get("net") is None:
            unassigned.append("%s.%s has no net (want %s)" % (ref, pin, net))
        elif not F.same_net(p["net"], net):
            bad.append("%s.%s is on %s, design says %s" % (ref, pin, p["net"], net))
    problems = bad + unassigned
    return Check("t1.pad-nets", 1, "Pad nets match the design source",
                 FAIL if problems else PASS,
                 "%d of %d pin assignments wrong" % (len(problems), len(want))
                 if problems else "%d pin assignments match" % len(want),
                 problems, critical=True)


def golden_netlist(net_path, design):
    """The golden netlist as a tiered check, so one report covers every stage."""
    from . import _sexp_min as sp
    from .gates import golden_table
    p = pathlib.Path(net_path)
    if not p.exists():
        return Check("t1.golden-netlist", 1, "Golden netlist", UNKNOWN,
                     "%s not exported — connectivity never compared" % p.name,
                     critical=True)
    ok, hit, total, line = golden_table(p, design.NETS, sp, verbose=False)
    return Check("t1.golden-netlist", 1, "Golden netlist", PASS if ok else FAIL,
                 line, critical=True)


# ── relation checks (tier 2) ───────────────────────────────────────────────────

def _resolve(facts, sel, near=None, exclude_ref=None):
    """Selector → ((x, y), label) or (None, reason). Selectors come from relations.py."""
    if "pad" in sel:
        p = F.pad(facts, sel["ref"], sel["pad"])
        if p is None:
            return None, "%s.%s is not on the board" % (sel["ref"], sel["pad"])
        return tuple(p["pos"]), "%s.%s" % (sel["ref"], sel["pad"])
    if "ref" in sel and "net" in sel:
        cand = F.pads(facts, ref=sel["ref"], net=sel["net"])
        if not cand:
            return None, "%s has no pad on %s" % (sel["ref"], sel["net"])
        if near is not None and len(cand) > 1:
            cand.sort(key=lambda p: poly.dist(tuple(p["pos"]), near))
        return tuple(cand[0]["pos"]), "%s.%s(%s)" % (sel["ref"], cand[0]["pad"], sel["net"])
    if sel.get("nearest"):
        pool = []
        for p in F.pads(facts, net=sel["net"]):
            if p["ref"] != exclude_ref:
                pool.append((tuple(p["pos"]), "%s.%s" % (p["ref"], p["pad"])))
        if "via" in sel.get("kinds", ()):
            for v in F.vias(facts, net=sel["net"]):
                pool.append((tuple(v["pos"]), "via@%.2f,%.2f" % tuple(v["pos"])))
        if not pool:
            return None, ("net %s has no other pad or via — the return path cannot be "
                          "measured" % sel["net"])
        if near is None:
            return pool[0]
        return min(pool, key=lambda e: poly.dist(e[0], near))
    return None, "unsupported selector %r" % (sel,)


def _relation_check(facts, c):
    """Evaluate one primitive constraint from relations.expand()."""
    cid = "t2.rel." + c["id"]
    crit = True

    if c["kind"] in ("near", "near-part"):
        if c["kind"] == "near-part":
            fa, fb = F.footprint(facts, c["a"]), F.footprint(facts, c["b"])
            if fa is None or fb is None:
                return Check(cid, 2, c["why"], UNKNOWN,
                             "%s or %s is not on the board" % (c["a"], c["b"]), critical=crit)
            pa, la = tuple(fa["pos"]), c["a"]
            pb, lb = tuple(fb["pos"]), c["b"]
        else:
            pa, la = _resolve(facts, c["a"])
            if pa is None:
                return Check(cid, 2, c["why"], UNKNOWN, la, critical=crit)
            pb, lb = _resolve(facts, c["b"], near=pa,
                              exclude_ref=c["a"].get("ref"))
            if pb is None:
                return Check(cid, 2, c["why"], UNKNOWN, lb, critical=crit)
        d = poly.dist(pa, pb)
        ok = d <= c["max_mm"] + 1e-6
        return Check(cid, 2, c["why"], PASS if ok else FAIL,
                     "%s → %s = %.2f mm (limit %.2f)" % (la, lb, d, c["max_mm"]),
                     [c["why"]] if not ok else [], critical=crit)

    if c["kind"] == "facing":
        pads_ = [p for p in F.pads(facts, ref=c["part"]) if p.get("net")]
        if len(pads_) != 2:
            return Check(cid, 2, c["why"], NA,
                         "%s has %d connected pads; orientation is only defined for two"
                         % (c["part"], len(pads_)))
        tgt, tl = _resolve(facts, c["target"])
        if tgt is None:
            return Check(cid, 2, c["why"], UNKNOWN, tl, critical=crit)
        on_net = [p for p in pads_ if F.same_net(p.get("net"), c["net"])]
        other = [p for p in pads_ if not F.same_net(p.get("net"), c["net"])]
        if not on_net or not other:
            return Check(cid, 2, c["why"], UNKNOWN,
                         "%s has no pad on %s" % (c["part"], c["net"]), critical=crit)
        dn = poly.dist(tuple(on_net[0]["pos"]), tgt)
        do = poly.dist(tuple(other[0]["pos"]), tgt)
        return Check(cid, 2, c["why"], PASS if dn <= do else FAIL,
                     "%s pad %.2f mm from %s, return pad %.2f mm — %s"
                     % (c["net"], dn, tl, do,
                        "correct orientation" if dn <= do else
                        "part is turned the wrong way round"), critical=crit)

    if c["kind"] == "between":
        dev, dl = _resolve(facts, c["device"])
        exp, el = _resolve(facts, c["exposed"])
        pro, pl = _resolve(facts, c["protected"])
        for pt, lbl in ((dev, dl), (exp, el), (pro, pl)):
            if pt is None:
                return Check(cid, 2, c["why"], UNKNOWN, lbl, critical=crit)
        d_dev, d_pro = poly.dist(dev, exp), poly.dist(dev, pro)
        t = _along(exp, pro, dev)
        # Two conditions, because either alone is too easy to satisfy: a device 30 mm
        # from the connector and 2 mm from the transceiver is "nearer the connector than
        # the transceiver is" and still clamps nothing.
        ok = (d_dev <= d_pro) and (t is None or t <= 0.5)
        pos = "%.0f%% of the way from %s to %s" % (100 * t, el, pl) if t is not None else "?"
        return Check(cid, 2, c["why"], PASS if ok else FAIL,
                     "%s is %.2f mm from %s and %.2f mm from %s (%s) — %s"
                     % (dl, d_dev, el, d_pro, pl, pos,
                        "clamps at the exposed side" if ok else
                        "sits on the victim's side of the net"),
                     [] if ok else [c["why"]], critical=crit)

    return Check(cid, 2, c.get("why", c["id"]), UNKNOWN,
                 "unsupported constraint kind %r" % c["kind"], critical=crit)


def _along(a, b, p):
    """Projection of p onto a→b, normalised. None when a and b coincide."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    den = dx * dx + dy * dy
    if den < 1e-12:
        return None
    return ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / den


def relations(facts, design):
    """Check declared electrical groups against actual pad geometry.

    Distances are pad-centre to pad-centre. That is a proxy for loop area, not a
    measurement of it: it is exact about "this capacitor serves that pin" and
    approximate about the loop itself. Nothing here claims otherwise.
    """
    from . import relations as R
    groups = getattr(design, "GROUPS", {}) or {}
    if not groups:
        return [Check("t2.relations", 2, "Electrical relations declared", UNKNOWN,
                      "design.py declares no GROUPS — no supporting part is tied to the "
                      "pin it serves, so decoupling, protection and return paths are "
                      "unverified")]
    errs = R.validate(design)
    out = [Check("t1.relations-declared", 1, "Relation declarations consistent",
                 FAIL if errs else PASS,
                 "%d declaration errors" % len(errs) if errs
                 else "%d relations declared, all referring to real parts, pins and nets"
                      % len(groups), errs, critical=True)]
    if errs:
        return out                        # Geometry answers would be meaningless
    for c in R.expand(design):
        out.append(_relation_check(facts, c))
    return out


# ── layer, plane and domain checks (tiers 1-3) ─────────────────────────────────

def _rect_poly(spec):
    if "outline" in spec:
        return [tuple(p) for p in spec["outline"]]
    x0, y0, x1, y1 = spec["rect"]
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _intrusions(facts, region_poly, layers):
    """Everything copper that enters `region_poly` on any of `layers`."""
    hits = []
    for t in facts.get("tracks", []):
        if t["layer"] in layers and poly.stroke_touches_poly(
                tuple(t["start"]), tuple(t["end"]), t["width"] / 2, region_poly):
            hits.append("track %s on %s" % (t.get("net") or "?", t["layer"]))
    for v in facts.get("vias", []):
        span = F.via_layers(facts.get("copper_layers") or v["layers"], v["layers"])
        if set(span) & set(layers) and poly.stroke_touches_poly(
                tuple(v["pos"]), tuple(v["pos"]), v["diameter"] / 2, region_poly):
            hits.append("via %s at %.2f,%.2f" % (v.get("net") or "?", v["pos"][0], v["pos"][1]))
    for z in facts.get("zones", []):
        for ly, regs in (z.get("filled") or {}).items():
            if ly not in layers:
                continue
            for r in regs:
                if poly.region_overlaps_poly(r, region_poly):
                    hits.append("filled copper %s on %s" % (z.get("net") or "?", ly))
                    break
    for p in facts.get("pads", []):
        for ly in set(p.get("layers", ())) & set(layers):
            regions = (p.get("copper") or {}).get(ly)
            if regions is None:
                hits.append("pad %s.%s: copper geometry unavailable on %s; re-extract facts"
                            % (p["ref"], p["pad"], ly))
            elif any(poly.region_overlaps_poly(r, region_poly) for r in regions):
                hits.append("pad %s.%s" % (p["ref"], p["pad"]))
    return hits


def layers_and_planes(facts, physical, design):
    """Layer purpose, keepout coverage, domain isolation and reference-plane continuity."""
    from . import stack as S
    out = []
    if physical is None:
        out.append(Check("t1.physical-declared", 1, "Layer purpose declared", UNKNOWN,
                         "no src/physical.py — nothing states which layers are reference "
                         "planes, so no net's return path can be checked", critical=False))
        return out
    errs = S.validate(physical, design)
    crit_nets = S.declared(physical, "CRITICAL_NETS", {}) or {}
    out.append(Check("t1.physical-declared", 1, "Layer purpose declared",
                     FAIL if errs else PASS,
                     "%d declaration errors" % len(errs) if errs
                     else "%d copper layers, %d planes, %d critical nets"
                          % (len(S.declared(physical, "COPPER_LAYERS", []) or []),
                             len(S.declared(physical, "PLANES", {}) or {}),
                             len(crit_nets)), errs, critical=True))
    if errs:
        return out

    declared_layers = list(S.declared(physical, "COPPER_LAYERS", []) or [])
    actual = list(facts.get("copper_layers") or [])
    if not actual:
        out.append(Check("t1.stack-matches", 1, "Board stack matches the declaration",
                         UNKNOWN, "the snapshot records no copper layers", critical=True))
    else:
        same = actual == declared_layers
        out.append(Check("t1.stack-matches", 1, "Board stack matches the declaration",
                         PASS if same else FAIL,
                         "board has %s; physical.py declares %s"
                         % (", ".join(actual), ", ".join(declared_layers)) if not same
                         else "%d copper layers as declared" % len(actual), critical=True))

    out += _netclass_checks(facts, physical)
    out += _keepout_checks(facts, physical)
    out += _domain_checks(facts, physical, design)
    out += _plane_checks(facts, physical, crit_nets)

    plane_layers = [ly for ly, r in (S.declared(physical, "LAYER_ROLE", {}) or {}).items()
                    if r == "plane"]
    cuts = [t for t in facts.get("tracks", []) if t["layer"] in plane_layers]
    if plane_layers:
        length = sum(poly.dist(tuple(t["start"]), tuple(t["end"])) for t in cuts)
        out.append(Check("t3.plane-cuts", 3, "Tracks routed across plane layers",
                         PASS if not cuts else FAIL,
                         "%d track segments (%.1f mm) run on plane layers %s"
                         % (len(cuts), length, ", ".join(plane_layers)) if cuts
                         else "no tracks on plane layers",
                         sorted({"%s on %s" % (t.get("net"), t["layer"]) for t in cuts})))
    return out


def _netclass_checks(facts, physical):
    """Rules only count once they are on the board, not once they are in a file."""
    from . import stack as S
    want = S.declared(physical, "NETCLASSES", {}) or {}
    want_of = S.declared(physical, "NET_CLASS", {}) or {}
    if not want:
        return [Check("t1.netclasses", 1, "Net classes in force", UNKNOWN,
                      "no NETCLASSES declared — every net routes with KiCad defaults")]
    have = facts.get("netclasses") or {}
    have_of = facts.get("net_class_of") or {}
    if not have:
        return [Check("t1.netclasses", 1, "Net classes in force", UNKNOWN,
                      "the snapshot carries no net classes; cannot confirm the project "
                      "file took effect", critical=True)]
    problems, missing = [], []
    for name, spec in want.items():
        if name not in have:
            problems.append("class %s is not on the board" % name)
            continue
        for key, board_key in (("track_width", "track"), ("clearance", "clearance"),
                               ("via_diameter", "via"), ("via_drill", "via_hole")):
            if key in spec and board_key not in have[name]:
                missing.append("class %s has no extracted %s" % (name, board_key))
            elif key in spec:
                if abs(float(spec[key]) - float(have[name][board_key])) > 1e-6:
                    problems.append("%s.%s is %s on the board, declared %s"
                                    % (name, key, have[name][board_key], spec[key]))
    for net, cls in want_of.items():
        got = have_of.get(net, have_of.get("/" + net))
        if got is None:
            missing.append("net %s has no extracted class assignment" % net)
        elif got != cls:
            problems.append("net %s is in class %s, declared %s" % (net, got, cls))
    return [Check("t1.netclasses", 1, "Net classes in force",
                  FAIL if problems else UNKNOWN if missing else PASS,
                  "%d net class deviations" % len(problems) if problems
                  else "%d net class facts missing" % len(missing) if missing
                  else "%d classes applied, %d nets assigned" % (len(want), len(want_of)),
                  problems + missing, critical=True)]


def _keepout_checks(facts, physical):
    """An antenna clearance is a region on named layers, not a blank rectangle on top."""
    from . import stack as S
    out = []
    for name, ko in (S.declared(physical, "KEEPOUTS", {}) or {}).items():
        want_layers = S.keepout_layers(physical, ko)
        area = F.rule_area(facts, name)
        if area is None:
            out.append(Check("t1.keepout.%s" % name, 1, "Keepout %s" % name, FAIL,
                             "declared but absent from the board — nothing keeps copper out",
                             critical=True))
            continue
        missing = [ly for ly in want_layers if ly not in (area.get("layers") or [])]
        if missing:
            out.append(Check("t1.keepout.%s" % name, 1, "Keepout %s" % name, FAIL,
                             "covers %s but not %s — clearance is declared on layers the "
                             "rule area does not reach"
                             % (", ".join(area.get("layers") or []), ", ".join(missing)),
                             critical=True))
            continue
        hits = _intrusions(facts, _rect_poly(ko), want_layers)
        out.append(Check("t1.keepout.%s" % name, 1, "Keepout %s" % name,
                         FAIL if hits else PASS,
                         "%d copper items inside the keepout" % len(hits) if hits
                         else "clear on %s" % ", ".join(want_layers),
                         sorted(set(hits)), critical=True))
    return out


def _side_of(pt, band, axis):
    lo, hi = (band[0], band[2]) if axis == "x" else (band[1], band[3])
    v = pt[0] if axis == "x" else pt[1]
    if v < lo:
        return "left" if axis == "x" else "top"
    if v > hi:
        return "right" if axis == "x" else "bottom"
    return "inside"


def _domain_checks(facts, physical, design):
    """No copper may bridge two domains, and no part may sit on the wrong side."""
    from . import stack as S
    out = []
    domains = S.declared(physical, "DOMAINS", {}) or {}
    for name, bar in (S.declared(physical, "BARRIERS", {}) or {}).items():
        band = bar["rect"]
        axis = "x" if (band[2] - band[0]) <= (band[3] - band[1]) else "y"
        bpoly = _rect_poly(bar)
        layers = S.declared(physical, "COPPER_LAYERS", []) or []
        hits = _intrusions(facts, bpoly, layers)
        out.append(Check("t1.barrier.%s" % name, 1, "Isolation barrier %s" % name,
                         FAIL if hits else PASS,
                         "%d copper items cross or enter the barrier" % len(hits) if hits
                         else "barrier clear across %d layers" % len(layers),
                         sorted(set(hits)), critical=True))
        wrong = []
        for dom, side in (bar.get("sides") or {}).items():
            for net in (domains.get(dom, {}).get("nets") or []):
                for p in F.pads(facts, net=net):
                    got = _side_of(tuple(p["pos"]), band, axis)
                    if got != side:
                        wrong.append("%s.%s (%s) is %s of the barrier, domain %s is %s"
                                     % (p["ref"], p["pad"], net, got, dom, side))
        if bar.get("sides"):
            out.append(Check("t1.barrier.%s.sides" % name, 1,
                             "Domain membership across %s" % name,
                             FAIL if wrong else PASS,
                             "%d pads on the wrong side" % len(wrong) if wrong
                             else "every domain pad on its declared side",
                             wrong, critical=True))
    return out


def _plane_checks(facts, physical, crit_nets):
    """Reference-plane continuity for declared critical nets.

    What this proves: every segment of the net runs over filled copper of its reference
    net, on its reference layer. Because the snapshot carries the *filled* polygons, a
    plane split or a track cutting the plane appears as a hole and fails the test.

    What it does not prove: impedance. Geometry without a dielectric stack and a solver
    cannot establish 50 ohms, so that check reports unknown by construction.
    """
    from . import stack as S
    out = []
    planes = S.declared(physical, "PLANES", {}) or {}
    stitch_net = (S.declared(physical, "STITCHING", {}) or {}).get("net")
    for net, spec in sorted(crit_nets.items()):
        ref_layer = spec.get("reference")
        ref_net = planes.get(ref_layer)
        segs = F.tracks(facts, net=net)
        regions = F.plane_regions(facts, ref_net, ref_layer) if ref_net else []
        if not segs:
            out.append(Check("t2.plane.%s" % net, 2, "%s over its reference plane" % net,
                             NA, "%s is not routed yet" % net))
        elif not regions:
            out.append(Check("t2.plane.%s" % net, 2, "%s over its reference plane" % net,
                             UNKNOWN,
                             "%s carries no filled copper on %s — the reference plane "
                             "cannot be located" % (ref_net, ref_layer), critical=True))
        else:
            bad = [t for t in segs
                   if not poly.seg_inside_any(tuple(t["start"]), tuple(t["end"]), regions)]
            out.append(Check("t2.plane.%s" % net, 2, "%s over its reference plane" % net,
                             FAIL if bad else PASS,
                             "%d of %d segments are not backed by %s on %s"
                             % (len(bad), len(segs), ref_net, ref_layer) if bad
                             else "all %d segments backed by %s on %s"
                                  % (len(segs), ref_net, ref_layer),
                             ["%s → %s on %s" % (t["start"], t["end"], t["layer"])
                              for t in bad], critical=True))

        net_vias = F.vias(facts, net=net)
        budget = spec.get("max_vias")
        if budget is not None:
            out.append(Check("t2.plane.%s.vias" % net, 2, "%s via budget" % net,
                             FAIL if len(net_vias) > budget else PASS,
                             "%d layer changes (budget %d)" % (len(net_vias), budget),
                             critical=True))
        out += _stitch_check(facts, physical, net, net_vias, spec, ref_net, stitch_net)

        if spec.get("impedance"):
            # Deliberately never PASS. A width and a plane are not an impedance: that
            # needs the dielectric stack and a solver, and neither is in this repository.
            ev = spec.get("impedance_evidence")
            out.append(Check("t2.impedance.%s" % net, 2,
                             "%s controlled impedance" % net, UNKNOWN,
                             "target %s ohm: no dielectric stackup or solver result on "
                             "record, so geometry alone cannot establish it"
                             % spec["impedance"],
                             ["asserted elsewhere: %s" % ev] if ev else [], critical=True))
    return out


def _stitch_check(facts, physical, net, net_vias, spec, ref_net, stitch_net):
    """Return-path transition at each layer change of a critical net.

    Never a blanket "a ground via beside every signal via": the companion via must carry
    the reference net **of this net's own domain**, and must not fall inside a rule area.
    A blanket rule is how stitching ends up bridging an isolation barrier.
    """
    from . import stack as S
    rid = "t2.return.%s" % net
    radius = spec.get("stitch_mm") or (S.declared(physical, "STITCHING", {}) or {}).get("max_mm")
    ret_net = spec.get("return") or ref_net or stitch_net
    if not net_vias:
        return [Check(rid, 2, "%s return path at layer changes" % net, NA,
                      "%s changes layer nowhere" % net)]
    if not radius or not ret_net:
        return [Check(rid, 2, "%s return path at layer changes" % net, UNKNOWN,
                      "%d layer changes, but no stitching radius or return net is "
                      "declared for %s" % (len(net_vias), net), critical=True)]
    # A return net in another domain is rejected by stack.validate at tier 1: no routing
    # can fix it. What is left to check here is geometry.
    areas = [_rect_poly(a) for a in facts.get("rule_areas", [])
             if a.get("outline") or a.get("rect")]
    pool = []
    for v in F.vias(facts, net=ret_net):
        # A via inside a rule area is not a return path: that is where a blanket
        # "ground via beside every signal via" rule does its damage.
        if any(poly.point_in_poly(tuple(v["pos"]), a) for a in areas if a):
            continue
        pool.append(tuple(v["pos"]))
    missing = []
    for v in net_vias:
        p = tuple(v["pos"])
        near = min((poly.dist(p, q) for q in pool), default=None)
        if near is None or near > radius:
            missing.append("via at %.2f,%.2f has no %s via within %.2f mm%s"
                           % (p[0], p[1], ret_net, radius,
                              "" if near is None else " (nearest %.2f mm)" % near))
    return [Check(rid, 2, "%s return path at layer changes" % net,
                  FAIL if missing else PASS,
                  "%d of %d layer changes lack a %s return via within %.2f mm"
                  % (len(missing), len(net_vias), ret_net, radius) if missing
                  else "all %d layer changes have a %s return via within %.2f mm"
                       % (len(net_vias), ret_net, radius),
                  missing, critical=True)]


# ── appearance (tier 4) ────────────────────────────────────────────────────────

def appearance(facts, physical=None):
    """Alignment and readability. Reported, never gating, never aggregated.

    These are the items that must never be able to offset a tier 1-2 result. Keeping
    them in their own tier is what makes that structural rather than a matter of
    judgement: there is no total to trade against.
    """
    from . import stack as S
    out = []
    odd = sorted(f["ref"] for f in facts.get("footprints", [])
                 if abs((float(f.get("rot", 0)) % 90.0)) > 1e-6)
    out.append(Check("t4.orientation", 4, "Parts on 90° orientations",
                     FAIL if odd else PASS,
                     "%d parts at off-axis angles" % len(odd) if odd
                     else "every part at a multiple of 90°", odd))
    grid = S.declared(physical, "PLACE_GRID", None)
    if not grid:
        out.append(Check("t4.place-grid", 4, "Placement grid", NA,
                         "no PLACE_GRID declared"))
    else:
        off = sorted(f["ref"] for f in facts.get("footprints", [])
                     if any(abs((c / grid) - round(c / grid)) > 1e-6 for c in f["pos"]))
        out.append(Check("t4.place-grid", 4, "Placement grid",
                         FAIL if off else PASS,
                         "%d parts off the %.3f mm grid" % (len(off), grid) if off
                         else "every part on the %.3f mm grid" % grid, off))
    return out
