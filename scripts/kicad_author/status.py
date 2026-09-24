"""Four-state check results, hard/soft tiers, and reports bound to a board file.

Two rules give this module its shape, both from observed AI-output failure modes:

  1. **Four states, not two.** pass / fail / unknown / n/a. Without `unknown`, a check
     that lacks its input has to pretend: "decoupling is fine" because a capacitor is
     nearby, or "the RF feed is 50 Ohm" with no dielectric stackup on record. A missing
     premise is a reportable state, not a pass.
  2. **Tiers are gates, never a score.** Hard constraints (tier 1-2) gate the build;
     optimisation targets (tier 3-4) are reported and never aggregated. There is
     deliberately no total score: a sum would let "shorter tracks, fewer vias" offset
     copper in an antenna keepout, which is precisely the trade that must not exist.

`unknown` must not become an escape hatch: an unknown on an item the project declared
critical gates the build exactly like a failure. The only way past it is a named waiver.
"""
import datetime
import hashlib
import json
import pathlib

PASS, FAIL, UNKNOWN, NA = "pass", "fail", "unknown", "n/a"
STATES = (PASS, FAIL, UNKNOWN, NA)

TIERS = {
    1: "Connectivity, mechanical, isolation and device hard constraints",
    2: "Critical loops, return paths, power and signal requirements",
    3: "Detours, layer changes and congestion",
    4: "Alignment, silkscreen and appearance",
}
GATING_TIERS = (1, 2)

MARK = {PASS: "pass", FAIL: "FAIL", UNKNOWN: "unkn", NA: "n/a "}


class Check:
    """One verdict. `critical` marks an item whose `unknown` is as bad as a failure."""

    def __init__(self, id, tier, title, status, detail="", evidence=(), critical=False):
        if status not in STATES:
            raise ValueError("bad status: %r" % status)
        if tier not in TIERS:
            raise ValueError("bad tier: %r" % tier)
        self.id, self.tier, self.title = id, tier, title
        self.status, self.detail = status, detail
        self.evidence = list(evidence)
        self.critical = critical
        self.waived_because = None

    def gates(self):
        """True when this verdict must stop the build."""
        if self.waived_because:
            return False
        if self.status == FAIL:
            return self.tier in GATING_TIERS
        if self.status == UNKNOWN:
            return self.critical
        return False

    def as_dict(self):
        d = {"id": self.id, "tier": self.tier, "title": self.title,
             "status": self.status, "detail": self.detail,
             "critical": self.critical, "gates": self.gates()}
        if self.evidence:
            d["evidence"] = self.evidence
        if self.waived_because:
            d["waived_because"] = self.waived_because
        return d


def sha256_of(path):
    h = hashlib.sha256()
    with open(str(path), "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()


def bind_file(path):
    """Identify a file by content hash, so a report can never be read against a newer board."""
    p = pathlib.Path(path)
    st = p.stat()
    return {"path": p.name, "sha256": sha256_of(p), "bytes": st.st_size,
            "mtime": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}


class Report:
    """A set of verdicts bound to one board file version.

    The binding is the point: a report that does not name the sha256 it was computed
    from can outlive the board and be quoted after the next edit.
    """

    def __init__(self, artifact=None, stage="", waivers=None, version=None):
        self.artifact = artifact or {}
        self.stage = stage
        self.waivers = dict(waivers or {})
        self.version = version
        self.checks = []

    def add(self, check):
        if check.id in self.waivers:
            check.waived_because = self.waivers[check.id]
        self.checks.append(check)
        return check

    def add_many(self, checks):
        for c in checks:
            self.add(c)

    def counts(self):
        return {s: sum(1 for c in self.checks if c.status == s) for s in STATES}

    def gating(self):
        return [c for c in self.checks if c.gates()]

    def waived(self):
        return [c for c in self.checks if c.waived_because]

    def unused_waivers(self):
        ids = {c.id for c in self.checks}
        return sorted(set(self.waivers) - ids)

    def exit_code(self):
        return 1 if self.gating() else 0

    def as_dict(self):
        return {"schema": 1, "generator": "kicad-author", "version": self.version,
                "stage": self.stage, "artifact": self.artifact,
                "created": datetime.datetime.now().isoformat(timespec="seconds"),
                "tiers": {str(k): v for k, v in TIERS.items()},
                "counts": self.counts(),
                "gating": [c.id for c in self.gating()],
                "checks": [c.as_dict() for c in self.checks]}

    def write_json(self, path):
        pathlib.Path(path).write_text(json.dumps(self.as_dict(), indent=2) + "\n")

    def text(self):
        out = []
        b = self.artifact
        if b:
            out.append("Artifact  %s  sha256 %s  (%s bytes, %s)"
                       % (b.get("path", "?"), (b.get("sha256") or "?")[:16],
                          b.get("bytes", "?"), b.get("mtime", "?")))
        if self.stage:
            out.append("Stage  %s" % self.stage)
        for tier in sorted(TIERS):
            group = [c for c in self.checks if c.tier == tier]
            if not group:
                continue
            note = "" if tier in GATING_TIERS else "  (reported, never gating)"
            out.append("Tier %d · %s%s" % (tier, TIERS[tier], note))
            for c in group:
                flag = " ←gates" if c.gates() else (" (waived)" if c.waived_because else "")
                out.append("  %s  %-28s %s%s" % (MARK[c.status], c.id,
                                                 c.detail or c.title, flag))
                for e in c.evidence[:6]:
                    out.append("          · %s" % e)
                if len(c.evidence) > 6:
                    out.append("          · … %d more" % (len(c.evidence) - 6))
        n = self.counts()
        out.append("Summary  pass %d  fail %d  unknown %d  n/a %d  →  %d gating"
                   % (n[PASS], n[FAIL], n[UNKNOWN], n[NA], len(self.gating())))
        for c in self.waived():
            out.append("  waived  %s: %s" % (c.id, c.waived_because))
        for w in self.unused_waivers():
            out.append("  ⚠ waiver %s matches no check (stale?)" % w)
        return "\n".join(out)
