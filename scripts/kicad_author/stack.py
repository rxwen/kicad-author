"""Layer purpose, net classes and generated design rules, declared in src/physical.py.

Handing a board to an autorouter and asking only for "all nets connected" is a decision,
not the absence of one: it silently accepts default track widths and default clearances for
every net, including supplies and controlled-impedance pairs. This module makes the decision
explicit and machine-readable **before** routing starts:

    COPPER_LAYERS   the stack, in order — checked against the board's enabled layers
    LAYER_ROLE      signal | plane | mixed  — what each layer is for
    PLANES          plane layer → the net it carries
    CRITICAL_NETS   net → which plane it references, its via budget, its stitching radius
    NETCLASSES      widths and clearances, emitted into <board>.kicad_pro
    DOMAINS         net groups that must not be bridged (isolation, separate grounds)
    KEEPOUTS        regions no copper may enter, on named layers rather than "the top one"

Two rules are encoded here rather than left to judgement:

  · A keepout that names layers must be checked against the layers that actually exist.
    A blank rectangle on F.Cu is not an antenna clearance on a four-layer board.
  · Via stitching is never blanket. A rule like "a ground via beside every signal via"
    bridges isolation domains; stitching is emitted per critical net, within one domain,
    and never inside a rule area.
"""
ROLES = ("signal", "plane", "mixed")

NETCLASS_DEFAULTS = {
    "clearance": 0.2, "track_width": 0.2, "via_diameter": 0.6, "via_drill": 0.3,
    "diff_pair_width": 0.2, "diff_pair_gap": 0.25,
    "microvia_diameter": 0.3, "microvia_drill": 0.1,
}


def declared(physical, name, default=None):
    return getattr(physical, name, default) if physical is not None else default


def layer_role(physical, layer):
    return (declared(physical, "LAYER_ROLE", {}) or {}).get(layer)


def plane_of(physical, layer):
    return (declared(physical, "PLANES", {}) or {}).get(layer)


def domain_of_net(physical, net):
    for dom, spec in (declared(physical, "DOMAINS", {}) or {}).items():
        if net in (spec.get("nets") or ()) or net.lstrip("/") in (spec.get("nets") or ()):
            return dom
    return None


def validate(physical, design):
    """Declaration errors in the physical stack, independent of any board."""
    if physical is None:
        return ["no src/physical.py — layer purpose, net classes and reference planes "
                "are undeclared"]
    errs = []
    layers = declared(physical, "COPPER_LAYERS", []) or []
    roles = declared(physical, "LAYER_ROLE", {}) or {}
    planes = declared(physical, "PLANES", {}) or {}
    nets = getattr(design, "NETS", {}) or {}
    if not layers:
        errs.append("COPPER_LAYERS is empty")
    for ly in layers:
        if ly not in roles:
            errs.append("layer %s has no role in LAYER_ROLE" % ly)
        elif roles[ly] not in ROLES:
            errs.append("layer %s has role %r, expected one of %s"
                        % (ly, roles[ly], ", ".join(ROLES)))
    for ly, net in planes.items():
        if ly not in layers:
            errs.append("PLANES names %s, which is not in COPPER_LAYERS" % ly)
        elif roles.get(ly) != "plane":
            errs.append("PLANES puts %s on %s, whose role is %r — a plane layer must have "
                        "role 'plane'" % (net, ly, roles.get(ly)))
        if net not in nets:
            errs.append("plane net %s on %s is not in design.NETS" % (net, ly))
    for net, spec in (declared(physical, "CRITICAL_NETS", {}) or {}).items():
        if net not in nets:
            errs.append("CRITICAL_NETS names %s, which is not in design.NETS" % net)
        ref = spec.get("reference")
        if not ref:
            errs.append("critical net %s declares no reference layer" % net)
        elif ref not in planes:
            errs.append("critical net %s references %s, which carries no plane"
                        % (net, ref))
        else:
            # Caught here rather than on the board: no placement or routing can fix a net
            # whose reference plane lives in another domain, and stitching to it would
            # bridge the barrier the domains exist to keep.
            ret = spec.get("return") or planes[ref]
            d_net, d_ret = domain_of_net(physical, net), domain_of_net(physical, ret)
            if d_net and d_ret and d_net != d_ret:
                errs.append("critical net %s is in domain %s but references %s on %s, "
                            "which is in domain %s" % (net, d_net, ret, ref, d_ret))
    classes = declared(physical, "NETCLASSES", {}) or {}
    for net, cls in (declared(physical, "NET_CLASS", {}) or {}).items():
        if cls not in classes:
            errs.append("net %s is assigned to undeclared net class %r" % (net, cls))
        if net not in nets:
            errs.append("NET_CLASS names %s, which is not in design.NETS" % net)
    for name, ko in (declared(physical, "KEEPOUTS", {}) or {}).items():
        lys = ko.get("layers", "all")
        if lys != "all":
            for ly in lys:
                if ly not in layers:
                    errs.append("keepout %s names layer %s, which is not in the stack"
                                % (name, ly))
        if "rect" not in ko and "outline" not in ko:
            errs.append("keepout %s has neither rect nor outline" % name)
    return errs


def keepout_layers(physical, ko):
    lys = ko.get("layers", "all")
    return list(declared(physical, "COPPER_LAYERS", []) or []) if lys == "all" else list(lys)


def rect_points(rect):
    x0, y0, x1, y1 = rect
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# ── generated artefacts ────────────────────────────────────────────────────────

def netclass_settings(physical):
    """net_settings block for <board>.kicad_pro."""
    classes = declared(physical, "NETCLASSES", {}) or {}
    out = []
    for name in sorted(classes, key=lambda n: (n != "Default", n)):
        c = dict(NETCLASS_DEFAULTS)
        c.update(classes[name])
        c["name"] = name
        c.setdefault("pcb_color", "rgba(0, 0, 0, 0.000)")
        c.setdefault("schematic_color", "rgba(0, 0, 0, 0.000)")
        c.setdefault("wire_width", 6)
        c.setdefault("bus_width", 12)
        c.setdefault("line_style", 0)
        out.append(c)
    patterns = [{"netclass": cls, "pattern": net}
                for net, cls in sorted((declared(physical, "NET_CLASS", {}) or {}).items())]
    return {"classes": out, "netclass_patterns": patterns}


def write_project(pro_path, physical):
    """Merge net classes into the KiCad project file, leaving everything else alone."""
    import json
    import pathlib
    p = pathlib.Path(pro_path)
    doc = {}
    if p.exists():
        try:
            doc = json.loads(p.read_text())
        except Exception:
            doc = {}
    doc.setdefault("meta", {"filename": p.name, "version": 1})   # KiCad fills the rest
    ns = doc.setdefault("net_settings", {})
    ns.update(netclass_settings(physical))
    p.write_text(json.dumps(doc, indent=2) + "\n")
    return len(ns.get("classes", [])), len(ns.get("netclass_patterns", []))


def dru_rules(physical):
    """Custom rules that net classes alone cannot express."""
    rules = []
    domains = declared(physical, "DOMAINS", {}) or {}
    iso = declared(physical, "DOMAIN_CLEARANCE", None)
    if iso and len(domains) > 1:
        names = sorted(domains)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                ca = " || ".join("A.Net == '%s'" % n for n in domains[a].get("nets", []))
                cb = " || ".join("B.Net == '%s'" % n for n in domains[b].get("nets", []))
                if not (ca and cb):
                    continue
                rules.append(
                    '(rule "isolation %s to %s"\n'
                    '  (constraint clearance (min %smm))\n'
                    '  (condition "(%s) && (%s)"))' % (a, b, iso, ca, cb))
    for net, spec in sorted((declared(physical, "CRITICAL_NETS", {}) or {}).items()):
        if spec.get("min_track"):
            rules.append(
                '(rule "%s minimum width"\n'
                '  (constraint track_width (min %smm))\n'
                '  (condition "A.Net == \'%s\'"))' % (net, spec["min_track"], net))
        if spec.get("max_vias") is not None:
            rules.append('# %s: via budget %d — enforced by kicad-author check, not DRC'
                         % (net, spec["max_vias"]))
    rules += list(declared(physical, "EXTRA_RULES", []) or [])
    return rules


def write_dru(dru_path, physical):
    import pathlib
    rules = dru_rules(physical)
    body = "\n\n".join(rules)
    text = ("(version 1)\n\n"
            "# Generated by kicad-author from src/physical.py — do not edit by hand.\n"
            "# Net class widths and clearances live in the project file, not here.\n\n"
            + body + ("\n" if body else ""))
    pathlib.Path(dru_path).write_text(text)
    return len([r for r in rules if r.startswith("(rule")])
