---
name: kicad-author
description: >-
  Generate readable KiCad schematics and PCBs from code. The design source
  (parts and nets) is the single source of truth for both outputs. Exit-code
  validation gates and a golden netlist protect connectivity when drawings change.
  Use to create KiCad boards, modify existing code-defined boards, adjust schematic
  drawing and layout, or generate manufacturing files. Also use for questions about
  schematic readability, power symbols versus labels, stub lengths, and avoiding
  routing-induced shorts. Complements the read-only review tool kicad-happy.
---

# kicad-author

The official KiCad `kicad-cli` **has no authoring commands** (only `erc`/`drc`/
`export`/`import`/`render`/`upgrade`), and schematics lack even a Python API.
This skill fills that gap with reusable deterministic code, drawing conventions,
and validation gates that turn design intent into file contents.

## Getting started

1. **Read [references/schematic-layout-conventions.md](references/schematic-layout-conventions.md) first.**
   It contains drawing conventions and an incident log: §9 documents seven issues
   encountered during testing that can silently corrupt a netlist.
2. Run `scripts/kicad-author env` to locate kicad-cli, a Python interpreter with
   pcbnew, and the symbol and footprint libraries.
3. Prepare the project files below, then run generation and validation.

## Project contract

Projects provide **data**; the skill provides **mechanisms**. Keep them separate:

```
<project>/
├── src/design.py     ← Source of truth: what the board is
├── src/layout.py     ← Presentation: where it appears on the sheet
├── src/build.sh      ← Pipeline (copy from templates/)
├── lib/              ← Project libraries fetched with easyeda2kicad
└── build/ fab/       ← Generated outputs
```

### `src/design.py` (edit the design here)

| Name | Required | Contents |
|---|---|---|
| `PARTS` | ✅ | `{ref: (lib_id, value, footprint, lcsc, description)}` |
| `NETS` | ✅ | `{net: [(ref, pin), ...]}`; use actual symbol/footprint pin numbers |
| `NO_CONNECT` | ✅ | `[(ref, pin), ...]`; explicitly unused pins, never fake connections to silence warnings |
| `ZONES` | ✅ | `{zone: (title, [ref, ...])}`; also determines which nets use wires |
| `POWER_NETS` | ✅ | Nets that always use power symbols |
| `MPNS` | ⬜ | `{ref: manufacturer_part_number}` for the BOM |
| `POWER_SYM` | ⬜ | `{net: "power:XXX"}` overrides the default mapping; the Value supplies the net name, allowing symbol graphics to be reused |
| `PWR_FLAG_NETS` | ⬜ | `[(net, x, y)]`; needed for ground-only nets to avoid ERC `pin_not_driven` |
| `LOCAL_SYM_LIBS` | ⬜ | `{library_name: project_relative_kicad_sym_path}` |
| `ISOLATED_NETS` | ⬜ | Isolated-domain net names for the isolation barrier check |
| `PAPER`/`TITLE`/`REV` | ⬜ | Sheet settings |

### `src/layout.py` (edit presentation here)

| Name | Contents |
|---|---|
| `SCH_LAYOUT` | `{ref: (x, y, rot)}` schematic positions |
| `ZONE_RECT` | `{zone: (x0, y0, x1, y1)}` zone borders |
| `PCB_PLACE` | `{ref: (x, y, rot)}` PCB positions |

> **Drawing changes must not change the design source.** Separating these files
> provides the boundary that the golden netlist protects.

## Commands

```bash
scripts/kicad-author env                    # Detect the toolchain
scripts/kicad-author sch  --project <dir>   # Generate .kicad_sch
scripts/kicad-author gate --project <dir>   # Compare against the golden netlist
```

PCB helpers are provided as a library (`kicad_author.pcb`) because they must run
in KiCad's Python interpreter. A thin project-level `gen_pcb.py` wrapper calls
this library; see `templates/` for the build workflow.

## Validation gates (all must pass after each generation)

| Gate | Pass condition |
|---|---|
| `kicad-cli sch erc --exit-code-violations` | Exit code 0 |
| **Golden netlist** | Every pin-to-net assignment matches `design.py` |
| Stub collision check | Runs during generation; collisions abort generation |
| `kicad-cli pcb drc --schematic-parity --exit-code-violations` | Exit code 0 |
| Isolation barrier check (isolated boards) | Zero crossing tracks, vias inside the barrier, out-of-bounds pours, or pads in the wrong domain |

**The golden netlist is the foundation.** Changes to drawing style, layout, or
stub lengths must preserve every connection. It caught all seven incidents in
§9 of the conventions; following documentation alone proved insufficient.

## Boundaries

- **No automatic placement**: `layout.py` supplies component positions. Automatic
  placement remains difficult and often produces less readable results than manual placement.
- **No PCB autorouting**: use Freerouting (`ExportSpecctraDSN` → jar → `ImportSpecctraSES`).
- **No component library creation**: use `easyeda2kicad` to fetch symbols, footprints,
  and 3D models by LCSC part number.
- **No design review**: use `kicad-happy` as a read-only, independent source of
  information. Authoring and review are deliberately separate.
