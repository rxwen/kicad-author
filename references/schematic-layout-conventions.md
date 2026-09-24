# KiCad schematic drawing conventions

> KiCad has no official schematic drawing conventions. KLC (KiCad Library Convention)
> covers **symbol and footprint libraries**, not drawing style. ERC checks electrical
> correctness, not readability, and KiCad has no schematic lint tool. This document
> fills that gap for both AI agents and people.
>
> **Documentation expresses intent; code and validation gates enforce correctness.**
> The rules here have corresponding deterministic implementations and self-checks in
> `scripts/kicad_author/`. Following documentation alone has proved insufficient
> in testing (see the incident log in §9).

## 0. Coordinates and units

| Item | Value |
|---|---|
| Units | mm (native `.kicad_sch` units) |
| Sheet Y axis | **Downward** |
| Symbol library Y axis | **Upward** |
| Absolute pin coordinates | `(X + rx, Y − ry)`, where `(rx, ry)` is the library position rotated **counterclockwise** by `rot` about the origin |
| Pin orientation | `(lib_angle + rot) mod 360` |
| Grid | **1.27mm** (50mil). Snap all component origins, wire endpoints, and labels to the grid to avoid ERC `endpoint_off_grid` |

Pin angle → outward direction on the sheet (Y is already flipped):

| Library angle | Pin position | Outward direction (sheet coordinates) |
|---|---|---|
| 0 | Left of symbol | `(−1, 0)` left |
| 90 | Below symbol | `(0, +1)` down |
| 180 | Right of symbol | `(+1, 0)` right |
| 270 | Above symbol | `(0, −1)` up |

## 1. Functional zones

Give each functional subcircuit a rectangular zone with a **dashed border and title**:

```
(rectangle (start x0 y0) (end x1 y1)
  (stroke (width 0.2) (type dash) (color 180 60 180 1))
  (fill (type none)) (uuid ...))
(text "USB-C input and ESD" (at x0+2 y0+5 0)
  (effects (font (size 2.2 2.2) (color 180 60 180 1)) (justify left bottom)) (uuid ...))
```

- Define zone membership in the design source (`ZONES`), rather than deciding it
  while drawing: it also determines which nets use wires.
- Keep components entirely inside their zone. Reserve approximately 8mm at the top
  for the title, with no components overlapping it.
- Aim for 4–6 zones. Fewer than 3 is too coarse; more than 8 will not fit on A3.

## 2. Connections: wires within zones, labels across zones, symbols for power

This is the **core readability rule**. Choose one representation per net:

| Net type | Representation | Reason |
|---|---|---|
| **Power** (GND / +3V3 / VBUS / VISO / …) | **Power port symbols** | GND often has 30+ pins; wires become tangled and text labels crowd pin numbers |
| **Local to a zone** (all pins in one zone, 2–5 pins) | **Wires and junctions** | Makes the topology immediately visible |
| Cross-zone or too many pins | **Text labels** | Long wires create unnecessary crossings |

> Counterexample from v1: labels on all 131 pins produced zero ERC violations and
> a 100% correct netlist, but **no visible topology**. Electrical correctness alone
> does not ensure readability.

### 2.1 Power port symbols

**KiCad power symbols take their net names from `Value`, not the pin name**
(`power:GND` has an empty pin name, as verified in testing). For a custom power
net, reuse an existing symbol graphic and change its Value:

| Net | Suggested symbol | Notes |
|---|---|---|
| `GND` | `power:GND` | Graphic hangs **below** the pin |
| Secondary ground, e.g. `CAN_GND` | `power:GND2` | Distinct graphic **visually communicates isolation** |
| `+3V3` / `+5V` / `VBUS` | `power:+3V3` / `power:+5V` / `power:VBUS` | Graphic sits **above** the pin |
| Other positive supply, e.g. `VISO` | Reuse `power:+5V` and set Value to the net name | |

The symbol origin is its pin. Place it directly at the stub endpoint with `rot 0`.
Use reference `#PWRnnn`, `in_bom no`, and `on_board yes`.

### 2.2 GND / CAN_GND need PWR_FLAG

Nets containing only `power_in` pins (ground-only nets) trigger ERC `pin_not_driven`.
Place one `power:PWR_FLAG` on each such net to declare a source.

## 3. Pin lead-out stubs: use different lengths

Every pin that connects to a wire or label first gets a **nonzero** stub.
Place the label or power symbol at the stub endpoint.

| Purpose | Length | Reason |
|---|---|---|
| Pins connected with wires | **2.54mm** | Compact routing with fewer bends |
| Pins using labels or power symbols | **5.08mm** | Pin numbers sit outside the pin; short stubs cause labels to overlap them |

> ⚠️ **Do not use one length everywhere.** Changing all stubs from 2.54 to 5.08
> shorted `CC2` to `GND` when opposing stubs on adjacent components met.
> **Choose the net representation first, then determine its stub length.**

### 3.1 Stub endpoint collision check (required)

Stub endpoints from different nets at **the same coordinate silently short**.
ERC may miss this; netlist comparison detects it. Check before writing the file
and fail immediately on a collision:

```python
seen = {}
for net, stubs in stub_of.items():
    for s in stubs:
        k = (s.end_x, s.end_y)
        if seen.get(k, net) != net:
            sys.exit(f"Stub endpoint collision (short circuit): {k} : {seen[k]} × {net}")
        seen[k] = net
```

## 4. Placement: keep supporting components near the pins they serve

A good schematic makes connections immediately apparent, beyond simply aligning components.

- **Decoupling capacitors**: place beside the IC power pin, sharing its y coordinate
  for a horizontal connection or x coordinate for a vertical connection.
- **Pull-up/pull-down resistors**: derive the position from the target pin. For a
  vertical resistor, the pin 2 stub endpoint is `y = cy + 3.81 + STUB`; set this
  equal to the target pin's y coordinate for a straight connection.
- **RC reset networks**: resistor above, capacitor below, button to the side;
  align all three stub endpoints on one vertical trunk.
- **Two-pin parts default to vertical** (`Device:R`/`Device:C` pin 1 at the top).
  `Device:LED` is horizontal, with pin 1 on the left.

> ⚠️ **Keep the corridor between connected components clear.** A CC pull-down
> resistor placed between an ESD array and USB-C connector blocked USB differential
> pair routing until it was moved.

## 5. Orthogonal comb router

Route nets within a zone using one trunk with branches to each pin:

1. Collect the **stub endpoints**.
2. **Try both vertical and horizontal trunks**, rather than only the larger span.
3. Try trunk positions at endpoints, **between endpoints**, then farther out on both sides.
4. Check each candidate for conflicts; use the first one that passes every check.
5. Add a `junction` where a branch joins the trunk away from its endpoints.

### 5.1 Avoidance rules (all required)

| Rule | Explanation |
|---|---|
| Do not cross component bodies | Bounding box = pin bounding rectangle plus clearance (suggested: 1.6mm) |
| **Do not cross other nets' endpoints** | Crossing connects them, silently shorting the nets |
| **Do not overlap collinear wires from other nets** | Overlapping wires merge |
| Perpendicular crossings are **allowed** | Crossing without a junction does not connect wires |

> ⚠️ Observed failures: checking only component bodies allowed trunks to cross
> foreign stub endpoints, reducing netlist matches from 131 to 89. Trying only
> one trunk orientation blocked USB differential pairs. Omitting positions
> between endpoints also prevented routing.

### 5.2 Wired nets still need one naming label

**KiCad automatically names unlabeled nets, e.g. `Net-(R1-Pad1)`.** After routing,
place **one** matching net label on the trunk or any stub endpoint, rather than
one per pin. Otherwise, comparison by net name fails.

## 6. Pin electrical types

Symbols converted by `easyeda2kicad` have **all pins marked `unspecified`**, so ERC
cannot determine driver relationships and may incorrectly report `pin_not_driven`.
Correct the types using the datasheet:

| Pin | Type |
|---|---|
| Power input (VCC/VDD/GND) | `power_in` |
| Regulator or isolated supply output | `power_out` |
| MCU I/O | `bidirectional` |
| Pins marked NC in the datasheet | `no_connect` |
| Passive parts and connectors | `passive` |

For **duplicate power pins on one component** (e.g. dual LDO VOUT, USB-C VBUS,
or isolated VISO pins), mark only **one** as `power_out` and the others as `passive`.
Otherwise ERC reports `Pins of type Power output and Power output are connected`.

## 7. Explicit no-connects

Place `(no_connect (at x y) (uuid ...))` on intentionally unused pins.
Do not invent connections to silence warnings or delete physical component pins.

## 8. Validation gates (all must pass after each generation)

| Gate | Command | Pass condition |
|---|---|---|
| ERC | `kicad-cli sch erc --exit-code-violations` | Exit code 0 |
| **Golden netlist** | `kicad-author gate netlist` | Every pin-to-net assignment matches the design source; no differing nets or extra named nets |
| Stub collisions | Self-check during generation | Zero collisions |

> The golden netlist is the foundation. Drawing, layout, and stub-length changes
> **must preserve every connection**. Any change is a bug. It caught all of the
> documented incidents.

These three gates are the schematic stage of the tiered report
(`kicad-author check --stage sch`). The copper stages, the four-state model and the PCB
incident log are in [pcb-physical-conventions.md](pcb-physical-conventions.md).

## 9. Incident log (observed failures to avoid repeating)

| Symptom | Root cause | Lesson |
|---|---|---|
| Netlist matches fell from 131 to 89; several nets lost all pins | Wires lacked naming labels, so KiCad renamed the nets | §5.2 |
| Netlist matches fell from 131 to 68; power nets including `GND`/`+3V3` were affected | Trunks crossed other nets' stub endpoints | §5.1 |
| USB differential pairs could not route | Router tried only the trunk orientation with the larger span | §5 |
| USB differential pairs still could not route | Trunk candidates omitted positions between endpoints | §5 |
| `CC2` shorted to `GND` | Globally lengthened stubs met on adjacent components | §3, §3.1 |
| USB differential pairs could not route (third occurrence) | CC pull-down resistor blocked the corridor | §4 |
| Copper pour script reported “Net not found: /GND” | After switching to power symbols, global nets had no sheet-path prefix (`GND`, not `/GND`), while signal nets did | Try both forms when resolving net names |
