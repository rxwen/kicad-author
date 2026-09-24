# kicad-author

**Generate readable KiCad schematics and PCBs from code.** A Claude Code / Claude Agent Skill.

The official KiCad `kicad-cli` provides `erc` / `drc` / `export` / `import` /
`render` / `upgrade`, but **no authoring commands**. The `pcbnew` Python API covers
PCBs only; schematics lack an API. AI agents therefore tend to write disposable
scripts with inconsistent drawing styles, and layout adjustments can silently
break connectivity.

This skill provides four reusable pieces:

| Resource | Purpose |
|---|---|
| **Drawing conventions** | [`references/schematic-layout-conventions.md`](references/schematic-layout-conventions.md) fills the gap left by KLC, which covers symbol/footprint libraries, and ERC, which checks electrical correctness |
| **Physical conventions** | [`references/pcb-physical-conventions.md`](references/pcb-physical-conventions.md) covers the copper: electrical relations, layer purpose, reference planes, stitching, isolation, and the four-state check model |
| **Deterministic implementation** | `scripts/kicad_author/`: standard-library-only comb routing, tiered stub lengths, power symbols, S-expression reading/writing, exact polygon predicates, and net class / design rule generation |
| **Exit-code validation gates** | The **golden netlist** protects connectivity; a **tiered four-state report**, bound to the board file's sha256, protects everything physical |

> During development, seven connectivity errors occurred despite knowing the rules:
> trunks crossed other nets' endpoints, longer stubs shorted nets, and unlabeled
> wires caused KiCad to rename nets. **Validation gates caught every one.**
> Section 9 of the schematic conventions records each incident; section 8 of the physical
> conventions records the PCB-side ones, including a pipeline that saved boards without
> re-filling their pours and then checked the stale copper.

## What the checks guarantee

Reports are produced by checking the **saved, reloaded and re-filled board file**, never the
generator's own variables, and every report names the sha256 it was computed from — so a DRC
result cannot survive the next edit.

| Principle | Consequence |
|---|---|
| Four states: `pass` / `fail` / `unknown` / `n/a` | No stackup on record means the impedance check reports **unknown**, not a pass |
| Tiers are gates, not weights | No aggregate score exists, so "shorter tracks, fewer vias" can never offset copper in an antenna keepout |
| Hard constraints first | Connectivity, mechanical, isolation and declared device constraints gate before anything about routing quality is considered |
| `unknown` on a critical item gates | Only a named waiver, printed in every report, gets past it |
| Relations, not proximity | `GROUPS` records which pin a capacitor serves and where its return goes; a capacitor mounted backwards fails on orientation even when the distance passes |

## Installation (project-local)

Clone and symlink into the project's `.claude/skills/` without changing the global environment:

```bash
git clone https://github.com/rxwen/kicad-author.git .claude/vendor/kicad-author
ln -s ../vendor/kicad-author .claude/skills/kicad-author
```

Dependencies: Python 3.8+ (standard library only) and KiCad 9/10 `kicad-cli`.
PCB operations also require KiCad's Python interpreter with `pcbnew`.
Run `scripts/kicad-author env` to inspect the environment.

## Usage

```bash
scripts/kicad-author env                        # Detect the toolchain
scripts/kicad-author sch   --project <dir>      # design.py + layout.py → .kicad_sch
scripts/kicad-author gate  --project <dir>      # Check every pin against the golden netlist
scripts/kicad-author rules --project <dir>      # Net classes → .kicad_pro, rules → .kicad_dru
scripts/kicad-author facts --project <dir>      # Snapshot the saved board (needs pcbnew)
scripts/kicad-author check --project <dir> --stage sch|preroute|final
```

Projects provide **data** — `src/design.py` (what the board is, and how its parts relate),
`src/layout.py` (where things go) and `src/physical.py` (under what rules copper may exist);
the skill provides **mechanisms**. See [`SKILL.md`](SKILL.md) for the contract and
[`templates/`](templates/) for starter files.

The `preroute` stage matters most in practice: a protection device on the wrong side of a net
costs a part move before routing and a re-route afterwards.

## Boundaries

The following tasks are delegated to other tools or manual input:

| Task | Handled by |
|---|---|
| Automatic placement | Manual positions in `layout.py`; automatic layouts are often harder to read. Declared relations are **checked** against pad geometry, not solved for |
| PCB autorouting | [Freerouting](https://github.com/freerouting/freerouting) |
| Component libraries | [`easyeda2kicad`](https://github.com/uPesy/easyeda2kicad.py), fetching symbols, footprints, and 3D models by LCSC part number |
| Design review | [`kicad-happy`](https://github.com/aklofas/kicad-happy), read-only and deliberately separate. This skill checks its own declared intent against the artefact it produced; whether that intent was any good is review |

## License

MIT
