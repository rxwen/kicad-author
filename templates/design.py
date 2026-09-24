"""Single source of truth for <board name>. Edit the design here, not its presentation.

See the Project contract section in SKILL.md for the kicad-author contract.
"""

BOARD_NAME = "myboard"                  # Output filename (defaults to the project directory name)
PAPER, TITLE, REV = "A3", "My board", "A"

# ref: (lib_id, value, footprint, LCSC part number, description)
PARTS = {
    "U1": ("MCU_Module:MyMcu", "MYMCU", "lib:MyMcu", "C1234", "MCU"),
    "C1": ("Device:C", "100n", "Capacitor_SMD:C_0402_1005Metric", "C1525", "U1 decoupling"),
    "R1": ("Device:R", "10k", "Resistor_SMD:R_0402_1005Metric", "C25744", "EN pull-up"),
}

# net: [(ref, pin), ...]   Use actual symbol/footprint pin numbers
NETS = {
    "+3V3": [("U1", "1"), ("C1", "1"), ("R1", "1")],
    "GND":  [("U1", "2"), ("C1", "2")],
    "EN":   [("U1", "3"), ("R1", "2")],
}

NO_CONNECT = [("U1", "4")]              # Explicitly unused; do not invent connections to silence warnings

# zone: (title, [ref, ...])  —— Also determines which nets use wires
ZONES = {
    "mcu": ("MCU and decoupling", ["U1", "C1", "R1"]),
}

POWER_NETS = ["+3V3", "GND"]            # Always use power symbols

# —— Electrical relations: which pin a part serves, and where its return current goes.
# Position and rotation follow these pad relationships; a tidy row that breaks a loop is
# not an improvement. What is not declared here is reported as unknown, never as a pass.
# Kinds: decouple / protect / crystal / regulator — see references/pcb-physical-conventions.md §3
GROUPS = {
    "u1-vdd": ("decouple", {"cap": "C1", "ic": "U1", "pin": "1", "net": "+3V3",
                            "ret": "GND", "max_mm": 2.0, "ret_max_mm": 3.0}),
    # "can-tvs": ("protect", {"device": "D2", "net": "CANH",
    #                         "exposed": ("J2", "1"), "protected": ("U2", "7")}),
}

PWR_FLAG_NETS = [("GND", 20, 206)]      # Required for ground-only nets to avoid ERC pin_not_driven

# —— Optional settings below ——
# MPNS = {"U1": "MYMCU-N4R8"}
# POWER_SYM = {"VISO": "power:+5V"}     # Net name comes from Value; existing graphics can be reused
# LOCAL_SYM_LIBS = {"lib": "lib/myboard.kicad_sym"}
# Isolated boards: declare DOMAINS and BARRIERS in src/physical.py, not here —
# the barrier is a physical region, and the check needs the layers it spans.
