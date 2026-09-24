"""Physical implementation of <board name>: layers, net classes, planes, keepouts.

Third file, third question. design.py says **what** the board is, layout.py says **where**
things go, and physical.py says **under what rules** the copper is allowed to exist.
Layer purpose and net class widths are neither design nor presentation: they are the
technology choices a router and a DRC engine must be told about before they run.

Everything here is optional. What is not declared is reported as `unknown`, never as a pass.
"""

# —— The stack, in board order. Checked against the board's enabled copper layers.
COPPER_LAYERS = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]

# signal | plane | mixed — what each layer is for
LAYER_ROLE = {"F.Cu": "signal", "In1.Cu": "plane", "In2.Cu": "plane", "B.Cu": "signal"}

# Plane layer → the net it carries. `kicad-author rules` pours these in gen_pcb.py.
PLANES = {"In1.Cu": "GND", "In2.Cu": "+3V3"}
# PLANE_RECT = {"In1.Cu": (0.3, 0.3, 49.7, 39.7)}   # Default: board outline inset 0.3mm

# —— Net classes: widths and clearances, written into <board>.kicad_pro
NETCLASSES = {
    "Default": {"track_width": 0.2, "clearance": 0.2, "via_diameter": 0.6, "via_drill": 0.3},
    "Power":   {"track_width": 0.5, "clearance": 0.2, "via_diameter": 0.8, "via_drill": 0.4},
}
NET_CLASS = {"+3V3": "Power", "GND": "Power"}

# —— Critical nets: which plane each one references, and what it may spend to get there.
# `reference` must name a layer in PLANES. `stitch_mm` is the radius within which a layer
# change must find a return via of the reference net — per net, never a blanket rule.
CRITICAL_NETS = {
    # "CANH": {"reference": "In1.Cu", "return": "GND", "max_vias": 2, "stitch_mm": 3.0,
    #          "min_track": 0.25, "impedance": 60},
}

# —— Domains that must not be bridged (isolation, separate grounds)
DOMAINS = {
    # "main": {"nets": ["+3V3", "GND"]},
    # "iso":  {"nets": ["VISO", "CAN_GND"]},
}
DOMAIN_CLEARANCE = None            # mm; emits a clearance rule between every pair of domains
BARRIERS = {
    # "iso-gap": {"rect": (28.0, 0.0, 34.0, 40.0), "sides": {"main": "left", "iso": "right"}},
}

# —— Regions no copper may enter, on named layers. "all" means every copper layer.
KEEPOUTS = {
    # "antenna": {"rect": (40.0, 0.0, 50.0, 12.0), "layers": "all"},
}

# EXTRA_RULES = ['(rule "..." (constraint ...) (condition "..."))']

# —— Waivers: the only way past a gating unknown, and it must be a reason, not a flag.
# Record the external evidence here; it is printed in every report that skips the check.
WAIVERS = {
    # "t2.impedance.CANH": "60 ohm computed with the KiCad calculator on stackup "
    #                      "JLCJLC04161H-3313, 0.20mm over 0.21mm prepreg — 2026-09-20",
}
