#!/usr/bin/env python3
"""Generate <board>.kicad_pcb. Runs in KiCad's interpreter: `$KICAD_PY src/gen_pcb.py`.

Thin wrapper: every mechanism lives in kicad_author.pcb, every decision in src/*.py.
Copy into your project and edit the marked sections only.
"""
import os
import pathlib
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ / "src"))
sys.path.insert(0, os.environ.get("KICAD_AUTHOR_SCRIPTS", ""))

import pcbnew                                          # noqa: E402

import design                                          # noqa: E402
import layout                                          # noqa: E402
from kicad_author import pcb                           # noqa: E402

try:
    import physical                                    # Optional: layers and rules
except ImportError:
    physical = None

NAME = getattr(design, "BOARD_NAME", PROJ.name)
OUT = PROJ / f"{NAME}.kicad_pcb"

fp_libs = {"__kicad__": os.environ.get("KICAD_FOOTPRINT_DIR", "")}
for lib, rel in getattr(design, "LOCAL_FP_LIBS", {}).items():
    fp_libs[lib] = str(PROJ / rel)

board = pcbnew.BOARD()
pcb.apply_stackup(board, physical)                     # Copper layer count and names
pin2net = pcb.load_netlist_pin_map(PROJ / "build" / f"{NAME}.net")
pcb.declare_nets(board, pin2net)

uuids = pcb.load_uuids(PROJ / "src" / "uuids.json")    # Keeps schematic↔PCB links stable
placed, npth, unassigned = pcb.place_parts(
    board, design.PARTS, layout.PCB_PLACE, fp_libs, pin2net, uuids)

pcb.board_outline(board, layout.BOARD_W, layout.BOARD_H)
pcb.set_aux_origin_bottom_left(board, layout.BOARD_H)

# ── Copper pours and rule areas: declared in src/physical.py ───────────────────
pcb.apply_planes(board, physical, layout)
pcb.apply_keepouts(board, physical)

pcb.refill(board)                                      # Fill before saving, always
pcbnew.SaveBoard(str(OUT), board)
print(f"✓ {OUT.name}: {placed} parts / NPTH corrections {len(npth)} / "
      f"unassigned pads {unassigned or 'none'}")
if unassigned:
    sys.exit("Pads left without a net — fix design.NETS before routing")
