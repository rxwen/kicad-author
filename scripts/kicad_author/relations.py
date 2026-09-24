"""Electrical relations between parts, declared in design.py as `GROUPS`.

The problem this fixes: the design source could say *where* a part goes and, in a free-text
description field no code ever read, that C5 was "U1 decoupling". It could not say **which
supply pin C5 serves, in which power domain, or where its return current goes** — so no
check could tell a capacitor that serves U1 pin 12 from one that merely sits near U1.

A relation is declared once and expands into primitive, machine-checkable constraints:

    near    pad A within D mm of pad B          ("this capacitor serves *this* pin")
    facing  the pad on net N is the nearer of a two-pad part's pads to its target
            (orientation follows the pad relationship, not the tidy row)
    between a device sits on the exposed side of a net, not merely near the victim

Deliberately small: a relation kind exists only if it unlocks a concrete check. A richer
ontology that a human fills in by hand is one more place for the design to claim something
nobody verifies — the failure mode this whole skill exists to remove.

    GROUPS = {
      "u1-vdd12": ("decouple", {"cap": "C5", "ic": "U1", "pin": "12",
                                "net": "+3V3", "ret": "GND",
                                "max_mm": 2.0, "ret_max_mm": 3.0}),
      "can-tvs":  ("protect",  {"device": "D2", "net": "CANH",
                                "exposed": ("J2", "1"), "protected": ("U2", "7")}),
      "xtal":     ("crystal",  {"osc": "Y1", "caps": ["C8", "C9"], "ic": "U1",
                                "ret": "GND", "ret_at": ("U1", "20"), "max_mm": 3.0}),
      "ldo":      ("regulator",{"ref": "U3", "in_pin": "1", "out_pin": "5",
                                "in_cap": "C10", "out_cap": "C11",
                                "in_net": "+5V", "out_net": "+3V3", "ret": "GND"}),
    }
"""
DEFAULTS = {"max_mm": 2.0, "ret_max_mm": 3.0, "osc_max_mm": 3.0}

KINDS = {
    "decouple":  {"need": ("cap", "ic", "pin", "net"), "opt": ("ret", "max_mm", "ret_max_mm")},
    "protect":   {"need": ("device", "net", "exposed", "protected"), "opt": ("max_mm",)},
    "crystal":   {"need": ("osc", "caps", "ic"), "opt": ("ret", "ret_at", "max_mm", "ret_max_mm")},
    "regulator": {"need": ("ref", "in_pin", "out_pin", "in_cap", "out_cap",
                           "in_net", "out_net"), "opt": ("ret", "max_mm", "ret_max_mm")},
}


def pad_sel(ref, pad):
    return {"ref": ref, "pad": str(pad)}


def net_sel(ref, net):
    """The pad of `ref` that sits on `net` — robust where pad numbering is not."""
    return {"ref": ref, "net": net}


def nearest_sel(net, kinds=("pad", "via")):
    """The closest connection point of `net`, used for return-path distance."""
    return {"net": net, "nearest": True, "kinds": list(kinds)}


def _refs(spec, *keys):
    out = []
    for k in keys:
        v = spec.get(k)
        if isinstance(v, (list, tuple)) and k in ("caps",):
            out += list(v)
        elif isinstance(v, (list, tuple)):
            out.append(v[0])
        elif v:
            out.append(v)
    return out


def validate(design):
    """Return a list of declaration errors, before any geometry is considered."""
    groups = getattr(design, "GROUPS", {}) or {}
    parts = set(getattr(design, "PARTS", {}))
    nets = getattr(design, "NETS", {}) or {}
    pin_of = {(r, str(p)) for pins in nets.values() for (r, p) in pins}
    net_of = {(r, str(p)): n for n, pins in nets.items() for (r, p) in pins}
    errs = []
    for name, decl in groups.items():
        if not (isinstance(decl, (list, tuple)) and len(decl) == 2):
            errs.append("%s: expected (kind, spec)" % name)
            continue
        kind, spec = decl
        if kind not in KINDS:
            errs.append("%s: unknown relation kind %r (have %s)"
                        % (name, kind, ", ".join(sorted(KINDS))))
            continue
        for k in KINDS[kind]["need"]:
            if k not in spec:
                errs.append("%s (%s): missing field %r" % (name, kind, k))
        for r in _refs(spec, "cap", "ic", "device", "osc", "caps", "ref",
                       "in_cap", "out_cap"):
            if r not in parts:
                errs.append("%s: %s is not in PARTS" % (name, r))
        for k in ("net", "ret", "in_net", "out_net"):
            if spec.get(k) and spec[k] not in nets:
                errs.append("%s: net %r is not in NETS" % (name, spec[k]))
        for k in ("exposed", "protected", "ret_at"):
            v = spec.get(k)
            if v and (v[0], str(v[1])) not in pin_of:
                errs.append("%s: %s pin %s.%s is not in NETS" % (name, k, v[0], v[1]))
        if kind == "decouple" and "pin" in spec and "net" in spec:
            key = (spec["ic"], str(spec["pin"]))
            if key in net_of and net_of[key] != spec["net"]:
                errs.append("%s: %s.%s is on %s, not the declared %s — the group names "
                            "the wrong supply pin" % (name, spec["ic"], spec["pin"],
                                                      net_of[key], spec["net"]))
        if kind == "protect":
            pins = {(r, str(p)) for (r, p) in nets.get(spec.get("net"), [])}
            for k in ("exposed", "protected"):
                v = spec.get(k)
                if v and (v[0], str(v[1])) not in pins:
                    errs.append("%s: %s pin %s.%s is not on net %s"
                                % (name, k, v[0], v[1], spec.get("net")))
            dev = spec.get("device")
            if dev and not any(r == dev for (r, _p) in nets.get(spec.get("net"), [])):
                errs.append("%s: protection device %s is not on net %s"
                            % (name, dev, spec.get("net")))
    return errs


def expand(design):
    """Relations → primitive constraints. Each carries the reason it exists."""
    groups = getattr(design, "GROUPS", {}) or {}
    nets = getattr(design, "NETS", {}) or {}
    out = []

    def add(**kw):
        out.append(kw)

    for name, decl in groups.items():
        if not (isinstance(decl, (list, tuple)) and len(decl) == 2):
            continue
        kind, spec = decl
        if kind not in KINDS:
            continue
        ret = spec.get("ret", "GND" if "GND" in nets else None)
        d = spec.get("max_mm", DEFAULTS["max_mm"])
        rd = spec.get("ret_max_mm", DEFAULTS["ret_max_mm"])

        if kind == "decouple":
            caps = spec["cap"] if isinstance(spec["cap"], (list, tuple)) else [spec["cap"]]
            for i, cap in enumerate(caps):
                sfx = "" if len(caps) == 1 else ".%s" % cap
                add(id="%s%s.serves" % (name, sfx), kind="near", group=name,
                    a=net_sel(cap, spec["net"]), b=pad_sel(spec["ic"], spec["pin"]),
                    max_mm=d, domain=spec.get("net"),
                    why="%s decouples %s.%s on %s" % (cap, spec["ic"], spec["pin"], spec["net"]))
                add(id="%s%s.facing" % (name, sfx), kind="facing", group=name,
                    part=cap, net=spec["net"], target=pad_sel(spec["ic"], spec["pin"]),
                    why="%s must present its %s pad to the supply pin, not its return pad"
                        % (cap, spec["net"]))
                if ret:
                    add(id="%s%s.return" % (name, sfx), kind="near", group=name,
                        a=net_sel(cap, ret), b=nearest_sel(ret), max_mm=rd, domain=ret,
                        why="return current of %s must reach %s without a detour" % (cap, ret))

        elif kind == "protect":
            add(id="%s.between" % name, kind="between", group=name,
                device=net_sel(spec["device"], spec["net"]),
                exposed=pad_sel(*spec["exposed"]), protected=pad_sel(*spec["protected"]),
                domain=spec["net"],
                why="%s must sit between the exposed pin %s.%s and the protected pin "
                    "%s.%s — proximity to the victim is not protection"
                    % (spec["device"], spec["exposed"][0], spec["exposed"][1],
                       spec["protected"][0], spec["protected"][1]))
            if spec.get("max_mm"):
                add(id="%s.at-connector" % name, kind="near", group=name,
                    a=net_sel(spec["device"], spec["net"]), b=pad_sel(*spec["exposed"]),
                    max_mm=spec["max_mm"], domain=spec["net"],
                    why="%s clamps at the connector" % spec["device"])

        elif kind == "crystal":
            osc_d = spec.get("max_mm", DEFAULTS["osc_max_mm"])
            for cap in spec["caps"]:
                add(id="%s.%s.load" % (name, cap), kind="near-part", group=name,
                    a=cap, b=spec["osc"], max_mm=osc_d, domain=spec.get("net"),
                    why="%s is a load capacitor of %s" % (cap, spec["osc"]))
                if ret and spec.get("ret_at"):
                    add(id="%s.%s.return" % (name, cap), kind="near", group=name,
                        a=net_sel(cap, ret), b=pad_sel(*spec["ret_at"]), max_mm=rd,
                        domain=ret,
                        why="load-capacitor return must close at %s.%s, not at the "
                            "nearest convenient ground" % (spec["ret_at"][0], spec["ret_at"][1]))
            add(id="%s.osc-near-ic" % name, kind="near-part", group=name,
                a=spec["osc"], b=spec["ic"], max_mm=spec.get("ic_max_mm", 10.0),
                why="oscillator loop stays short")

        elif kind == "regulator":
            for cap, pin, net, tag in ((spec["in_cap"], spec["in_pin"], spec["in_net"], "in"),
                                       (spec["out_cap"], spec["out_pin"], spec["out_net"], "out")):
                add(id="%s.%s" % (name, tag), kind="near", group=name,
                    a=net_sel(cap, net), b=pad_sel(spec["ref"], pin), max_mm=d, domain=net,
                    why="%s is the %sput capacitor of %s" % (cap, tag, spec["ref"]))
                add(id="%s.%s.facing" % (name, tag), kind="facing", group=name,
                    part=cap, net=net, target=pad_sel(spec["ref"], pin),
                    why="%s must present its %s pad to %s.%s" % (cap, net, spec["ref"], pin))
                if ret:
                    add(id="%s.%s.return" % (name, tag), kind="near", group=name,
                        a=net_sel(cap, ret), b=nearest_sel(ret), max_mm=rd, domain=ret,
                        why="%s return path" % cap)
    return out
