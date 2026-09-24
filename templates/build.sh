#!/usr/bin/env bash
# Full build: design.py → manufacturing files. Every stage ends in a tiered report
# bound to the artifact's sha256, so no result can be quoted after the next edit.
# Usage: bash src/build.sh [--no-route]
set -euo pipefail
cd "$(dirname "$0")/.."

KA_HOME="${KICAD_AUTHOR_HOME:-$(cd .. && pwd)/.claude/skills/kicad-author}"
KA="$KA_HOME/scripts/kicad-author"
[ -x "$KA" ] || { echo "Cannot find kicad-author: $KA"; exit 1; }
export KICAD_AUTHOR_SCRIPTS="$KA_HOME/scripts"
eval "$("$KA" env --export)"          # Export KICAD_CLI / KICAD_PY / library dirs

mkdir -p build fab
NAME=$(python3 -c "import sys;sys.path.insert(0,'src');import design;print(design.BOARD_NAME)")

echo "── 1/9 Generate schematic ──"
"$KA" sch --project .

echo "── 2/9 ERC + netlist export ──"
# Both severities: warnings are reported at tier 3, never silently dropped.
"$KICAD_CLI" sch erc --format json --severity-error --severity-warning \
    -o build/erc.json "$NAME.kicad_sch" || true
"$KICAD_CLI" sch export netlist --format kicadsexpr -o "build/$NAME.net" "$NAME.kicad_sch" >/dev/null

echo "── 3/9 Gate: schematic stage ──"
"$KA" check --project . --stage sch

echo "── 4/9 Layers, net classes and custom rules ──"
"$KA" rules --project .

echo "── 5/9 Generate PCB ──"
"$KICAD_PY" src/gen_pcb.py            # Thin wrapper around kicad_author.pcb

echo "── 6/9 Gate: critical placement, BEFORE routing ──"
# Placement problems found after routing cost a re-route; found here they cost a move.
"$KA" facts --project . --out build/facts-preroute.json
"$KA" check --project . --stage preroute --facts build/facts-preroute.json

if [ "${1:-}" != "--no-route" ]; then
  echo "── 7/9 Autorouting (Freerouting) ──"
  "$KICAD_PY" -c "import pcbnew;b=pcbnew.LoadBoard('$NAME.kicad_pcb');pcbnew.ExportSpecctraDSN(b,'build/$NAME.dsn')"
  "${JAVA_BIN:-java}" -jar "$FREEROUTING_JAR" -de "build/$NAME.dsn" -do "build/$NAME.ses" -mt 1 -mp 30
  "$KICAD_PY" -c "
import pcbnew
b=pcbnew.LoadBoard('$NAME.kicad_pcb')
assert pcbnew.ImportSpecctraSES(b,'build/$NAME.ses')
pcbnew.SaveBoard('$NAME.kicad_pcb',b)"
fi

echo "── 8/9 Finalize: refill pours, save, snapshot the saved board ──"
# --finalize refills before hashing: an ImportSpecctraSES + SaveBoard leaves the
# pre-route fills on disk, and every copper answer after that is about a stale board.
"$KA" facts --project . --finalize --out build/facts.json

echo "── 9/9 Gate: DRC + final report ──"
"$KICAD_CLI" pcb drc --format json --schematic-parity \
    --severity-error --severity-warning -o build/drc.json "$NAME.kicad_pcb" || true
"$KA" check --project . --stage final --facts build/facts.json

echo "✅ Build passed — report: build/report-final.json"
