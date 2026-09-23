"""Presentation for <board name>: sheet and PCB positions. Edit layout here without changing the design."""

# —— Schematic (mm, Y downward, 1.27 grid)
# zone: (x0, y0, x1, y1) Dashed border; reserve ~8mm at the top for the title, clear of components
ZONE_RECT = {
    "mcu": (20.0, 20.0, 120.0, 100.0),
}

# ref: (x, y, rot)
SCH_LAYOUT = {
    "U1": (60.0, 60.0, 0),
    "C1": (95.0, 55.0, 0),              # Keep supporting parts close to the pins they serve
    "R1": (95.0, 70.0, 0),
}

# —— PCB (mm, origin at top left of board)
BOARD_W, BOARD_H = 50.0, 40.0

PCB_PLACE = {
    "U1": (25.0, 20.0, 0),
    "C1": (32.0, 15.0, 0),
    "R1": (32.0, 25.0, 0),
}
