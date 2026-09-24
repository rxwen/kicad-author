---
name: kicad-author
description: >-
  Generate readable KiCad schematics and PCBs from code. The design source (parts, nets
  and the electrical relations between them) is the single source of truth; layer purpose
  and net classes are declared before routing, not left to the autorouter. Tiered
  four-state checks run against the saved, re-filled board file and are bound to its
  hash, so hard constraints gate the build and missing evidence reports as unknown rather
  than as a pass. Use to create KiCad boards, modify existing code-defined boards, adjust
  schematic drawing and layout, declare reference planes and keepouts, or generate
  manufacturing files. Also use for questions about schematic readability, power symbols
  versus labels, stub lengths, decoupling and protection placement, reference planes and
  return paths, or isolation barriers. Complements the read-only review tool kicad-happy.
---

# kicad-author

The official KiCad `kicad-cli` **has no authoring commands** (only `erc`/`drc`/
`export`/`import`/`render`/`upgrade`), and schematics lack even a Python API.
This skill fills that gap with reusable deterministic code, drawing conventions,
and validation gates that turn design intent into file contents.

## Getting started

1. **Read the two convention documents first.**
   [schematic-layout-conventions.md](references/schematic-layout-conventions.md) covers the
   drawing; §9 logs seven incidents that silently corrupted a netlist.
   [pcb-physical-conventions.md](references/pcb-physical-conventions.md) covers the copper:
   four-state checks, tiers, relations, reference planes, and §8 logs the PCB-side incidents.
2. Run `scripts/kicad-author env` to locate kicad-cli, a Python interpreter with
   pcbnew, and the symbol and footprint libraries.
3. Prepare the project files below, then run generation and validation.

## Project contract

Projects provide **data**; the skill provides **mechanisms**. Three files, three questions:
what the board is, where things go, under what rules copper may exist.

```
<project>/
├── src/design.py     ← Source of truth: what the board is, and how its parts relate
├── src/layout.py     ← Presentation: where it appears on the sheet and on the board
├── src/physical.py   ← Physical rules: layers, planes, net classes, keepouts, domains
├── src/gen_pcb.py    ← Thin pcbnew wrapper (copy from templates/)
├── src/build.sh      ← Pipeline (copy from templates/)
├── lib/              ← Project libraries fetched with easyeda2kicad
└── build/ fab/       ← Generated outputs, reports and facts snapshots
```

### `src/design.py` (edit the design here)

| Name | Required | Contents |
|---|---|---|
| `PARTS` | ✅ | `{ref: (lib_id, value, footprint, lcsc, description)}` |
| `NETS` | ✅ | `{net: [(ref, pin), ...]}`; use actual symbol/footprint pin numbers |
| `NO_CONNECT` | ✅ | `[(ref, pin), ...]`; explicitly unused pins, never fake connections to silence warnings |
| `ZONES` | ✅ | `{zone: (title, [ref, ...])}`; also determines which nets use wires |
| `POWER_NETS` | ✅ | Nets that always use power symbols |
| `GROUPS` | ⬜ | `{name: (kind, spec)}` electrical relations — **which pin a part serves, in which domain, where its return goes.** `decouple`, `protect`, `crystal`, `regulator`. Undeclared relations report as unknown, never as a pass |
| `MPNS` | ⬜ | `{ref: manufacturer_part_number}` for the BOM |
| `POWER_SYM` | ⬜ | `{net: "power:XXX"}` overrides the default mapping; the Value supplies the net name |
| `PWR_FLAG_NETS` | ⬜ | `[(net, x, y)]`; needed for ground-only nets to avoid ERC `pin_not_driven` |
| `LOCAL_SYM_LIBS` | ⬜ | `{library_name: project_relative_kicad_sym_path}` |
| `LOCAL_FP_LIBS` | ⬜ | `{library_name: project_relative_pretty_dir}` for project footprints |
| `PAPER`/`TITLE`/`REV` | ⬜ | Sheet settings |

### `src/layout.py` (edit presentation here)

| Name | Contents |
|---|---|
| `SCH_LAYOUT` | `{ref: (x, y, rot)}` schematic positions |
| `ZONE_RECT` | `{zone: (x0, y0, x1, y1)}` zone borders |
| `PCB_PLACE` | `{ref: (x, y, rot)}` PCB positions |
| `BOARD_W`/`BOARD_H` | Board outline |

### `src/physical.py` (edit physical rules here)

| Name | Contents |
|---|---|
| `COPPER_LAYERS` | The stack, checked against the board's enabled copper layers |
| `LAYER_ROLE` | `signal` / `plane` / `mixed` per layer |
| `PLANES` | Plane layer → the net it carries |
| `NETCLASSES` / `NET_CLASS` | Widths and clearances, written into `<board>.kicad_pro` |
| `CRITICAL_NETS` | Per net: reference layer, return net, via budget, `stitch_mm`, `min_track`, `impedance` |
| `DOMAINS` / `BARRIERS` / `DOMAIN_CLEARANCE` | Groups no copper may bridge, and the gaps between them |
| `KEEPOUTS` | Regions no copper may enter, **on named layers** (`"all"` resolves to the real stack) |
| `WAIVERS` | `{check_id: reason}` — the only way past a gating unknown, and it must be a reason |

> **Drawing changes must not change the design source.** Separating these files
> provides the boundary that the golden netlist protects.

## Commands

```bash
scripts/kicad-author env                        # Detect the toolchain
scripts/kicad-author sch   --project <dir>      # Generate .kicad_sch
scripts/kicad-author gate  --project <dir>      # Compare against the golden netlist
scripts/kicad-author rules --project <dir>      # Net classes → .kicad_pro, rules → .kicad_dru
scripts/kicad-author facts --project <dir>      # Snapshot the saved board (needs pcbnew)
scripts/kicad-author check --project <dir> --stage sch|preroute|final
```

`facts --finalize` refills every pour, saves, **then** hashes and snapshots the file.
`check` refuses to report when that hash no longer matches the board on disk.

PCB helpers are a library (`kicad_author.pcb`) because they must run in KiCad's Python
interpreter; `templates/gen_pcb.py` is the thin project-level wrapper.

## Validation gates

Every stage produces a report bound to its artefact's sha256. Checks report **pass /
fail / unknown / n/a** and are grouped into four tiers:

| Tier | Contents | Gates |
|---|---|---|
| 1 | Connectivity, mechanical, isolation, device hard constraints | ✅ |
| 2 | Critical loops, return paths, power and signal requirements | ✅ |
| 3 | Detours, layer changes, congestion | ❌ reported |
| 4 | Alignment, silkscreen, appearance | ❌ reported |

| Stage | Command | Must hold |
|---|---|---|
| Schematic | `check --stage sch` | ERC errors 0, **golden netlist** exact, no stub collisions |
| Pre-route | `check --stage preroute` | Tier 1-2 clear **before** routing: relations, keepout coverage, domains |
| Final | `check --stage final` | Tier 1-2 clear on the saved, re-filled board; DRC + schematic parity |

**The golden netlist is the foundation.** Changes to drawing style, layout, or
stub lengths must preserve every connection. It caught all seven incidents in
§9 of the schematic conventions; following documentation alone proved insufficient.

Three rules make the rest trustworthy, and each has tests in `tests/test_units.py`:

- **Tiers are gates, not weights.** There is no aggregate score, so "shorter tracks, fewer
  vias" can never offset copper in an antenna keepout or a broken reference plane.
- **`unknown` is a state, not a pass.** A missing stackup means the impedance check reports
  unknown; a missing `GROUPS` declaration means placement is reported as unverified.
- **An `unknown` on a declared-critical item gates the build.** Only a named `WAIVERS`
  entry, printed in every report, gets past it.

## Boundaries

- **No automatic placement**: `layout.py` supplies component positions. Automatic
  placement remains difficult and often produces less readable results than manual
  placement. Declared relations are **checked** against the resulting pad geometry, not
  solved for — a violation fails the build instead of moving the part.
- **No PCB autorouting**: use Freerouting (`ExportSpecctraDSN` → jar → `ImportSpecctraSES`).
  Run `rules` first, or the router is told only "connect everything".
- **No component library creation**: use `easyeda2kicad` to fetch symbols, footprints,
  and 3D models by LCSC part number.
- **No design review**: this skill checks its **own declared intent against the artefact it
  produced** — self-consistency, which belongs to authoring. Whether the intent was any good
  (is 60 Ω right, is this stack sensible) is review, and stays with `kicad-happy`, read-only
  and independent. Acquiring a check engine does not make this an EMC reviewer.
