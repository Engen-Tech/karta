#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Gate probe for sme-pack-static-suite: the full pack/mirror/pin static suite.

Composes the benchmarks/sme-static/ scripts (the single composer — no run.sh):
  PROBE 1  pack integrity — validate_packs.py over built-ins (exceptions.json
           applied) and each enrolled consumer repo's .karta/sme/, plus
           scripts/check_shared_copies.py mirror parity, plus cross_prefix.
  PROBE 2  pin derivation — match_pins.py replays the documented matching rule
           (contract mode = root detect_stack; coverage mode = manifest-dir
           union, diagnostic only) and emits the per-binder pin table with era
           partitioning over the enrolled consumer repos' binders.
  PROBE 3  seed drift — seed_drift.py classifies every overlay against the
           built-in's git history and audits match tokens for dead/overbroad
           entries over the recorded consumer corpus.

Emits the gate probe JSON contract
{"id","status":"pass"|"fail","partial","implemented_checks","findings","metrics"}
on stdout and exits 0 whether pass or fail (nonzero exit means the probe itself
crashed). Self-test prints [PASS]/[FAIL] lines and an N/N checks passed summary.

Status stance: karta-repo-owned zero-target surfaces (validator failures after
exceptions, mirror drift, prefix collisions, a matching-rule hash mismatch) and
a missing consumer directory in live mode flip status to "fail" directly
(fail-closed). Consumer-side findings (drift classes, dead/overbroad tokens,
pin non-conformance, invalid consumer packs) are recorded evidence:
status "fail" only on regression against the last committed results file
(a recorded finding id not present in the baseline). The baseline is read from
the committed blob, so the file this run writes never grades itself:
the regression baseline is the newest git-tracked results file for this vector (git ls-files), never an untracked or same-run file.
With no tracked baseline (bootstrap) consumer-side findings are recorded but do
not flip status — except that the first baseline must contain the seeded
findings named in the work-item contract; the probe fails closed when a seeded
finding is absent from its own first baseline.

Every live run writes the dated findings JSON to
benchmarks/sme/results/<run-date>-sme-pack-static-suite.json (overwriting a
same-date file). Consumer repos are addressed through --consumers <path,...>,
defaulting to the consumers.json names resolved as sibling directories of the
--target repo's parent.
"""
from __future__ import annotations
import argparse, datetime, json, re, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sme-static"))
import cross_prefix
import match_pins
import seed_drift

PROBE_ID = "sme-pack-static-suite"
VALIDATOR = Path("skills") / "karta-kaizen" / "scripts" / "validate_packs.py"
SHARED_COPIES = Path("scripts") / "check_shared_copies.py"
RESULTS_DIR = Path("benchmarks") / "sme" / "results"
RESULTS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-sme-pack-static-suite\.json$")
CHECK_TIMEOUT_S = 60
IMPLEMENTED_CHECKS = [
    "pack-format-validation (validate_packs.py, exceptions applied)",
    "mirror-parity (check_shared_copies.py)",
    "cross-prefix collision/foreign-use audit",
    "matching-rule sha256 guard (match_pins.py)",
    "per-binder pin derivation table with era partitioning",
    "seed-drift classification vs built-in git history",
    "dead/overbroad match-token audit",
    "baseline regression gate",
]
# The work-item contract's seeded findings: the first committed baseline must
# contain them, else the probe fails closed (a seeded truth its own probe cannot
# see means the probe is broken, not the repo clean).
SEEDED_IDS = (
    "seed-drift:gringotts:go-htmx:DIVERGENT",
    "dead-token:gringotts:go-htmx:templ",
    "overbroad-token:gringotts:go-htmx:go",
)
SEEDED_PREFIXES = ("pin-nonconformant:parchmark:",)


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, timeout=CHECK_TIMEOUT_S)
    return proc.stdout.strip() if proc.returncode == 0 else ""


# --- validator subprocess + parse ----------------------------------------------

def run_validator(target: Path, files: list[Path]) -> tuple[dict[str, list[str]], list[str], str | None]:
    """(invalid: file -> errors, warnings, crash). Runs validate_packs.py once."""
    if not files:
        return {}, [], None
    try:
        proc = subprocess.run(
            [sys.executable, str(target / VALIDATOR), *[str(f) for f in files]],
            capture_output=True, text=True, timeout=CHECK_TIMEOUT_S, cwd=str(target))
    except (OSError, subprocess.TimeoutExpired) as e:
        return {}, [], f"{VALIDATOR} did not complete ({e})"
    invalid, warnings = parse_validator_output(proc.stdout)
    return invalid, warnings, None


def parse_validator_output(stdout: str) -> tuple[dict[str, list[str]], list[str]]:
    invalid: dict[str, list[str]] = {}
    warnings: list[str] = []
    current: str | None = None
    for ln in stdout.splitlines():
        if ln.endswith(": INVALID"):
            current = ln[: -len(": INVALID")]
            invalid[current] = []
        elif ": unreadable (" in ln:
            invalid[ln.split(": unreadable (")[0]] = [ln]
        elif ": warning: " in ln:
            warnings.append(ln)
        elif ln.startswith("  - ") and current:
            invalid[current].append(ln[4:])
        elif not ln.startswith("  "):
            current = None
    return invalid, warnings


# --- era partitioning ----------------------------------------------------------

def load_release_tags(karta: Path) -> list[tuple[str, datetime.datetime]]:
    out = _git(karta, "for-each-ref", "refs/tags/v*",
               "--format=%(refname:short)|%(creatordate:iso8601-strict)")
    tags = []
    for ln in out.splitlines():
        name, _, date = ln.partition("|")
        if not re.fullmatch(r"v\d+\.\d+\.\d+", name):
            continue  # release tags only (validate-stable etc. are not eras)
        try:
            tags.append((name, datetime.datetime.fromisoformat(date)))
        except ValueError:
            continue
    return sorted(tags, key=lambda t: t[1])


def era_of(binder_date: datetime.datetime | None,
           tags: list[tuple[str, datetime.datetime]]) -> str:
    if not tags:
        return "untagged"
    if binder_date is None:  # untracked binder file: treat as now -> newest era
        return tags[-1][0]
    in_force = [name for name, dt in tags if dt <= binder_date]
    return in_force[-1] if in_force else "pre-" + tags[0][0]


# --- baseline selection + status decision --------------------------------------

def pick_baseline(tracked_names: list[str]) -> str | None:
    """Newest date-named results file for THIS vector among git-tracked names."""
    dated = sorted(n for n in tracked_names if RESULTS_RE.match(Path(n).name))
    return dated[-1] if dated else None


def decide_status(karta_failures: list[str], recorded_ids: set[str],
                  baseline_ids: set[str] | None) -> tuple[str, list[str]]:
    """(status, detail ids). baseline_ids None == bootstrap (no tracked baseline)."""
    if karta_failures:
        return "fail", []
    if baseline_ids is None:
        missing = [s for s in SEEDED_IDS if s not in recorded_ids]
        missing += [f"{p}*" for p in SEEDED_PREFIXES
                    if not any(i.startswith(p) for i in recorded_ids)]
        return ("fail", missing) if missing else ("pass", [])
    new = sorted(recorded_ids - baseline_ids)
    return ("fail", new) if new else ("pass", [])


# --- live run ------------------------------------------------------------------

def live(target: Path, consumer_paths: list[Path], run_date: str) -> dict:
    static_cfg = target / "benchmarks" / "sme-static"
    exceptions = {e["file"]: e["justification"] for e in json.loads(
        (static_cfg / "exceptions.json").read_text())["exceptions"]}

    karta_failures: list[str] = []   # fail-closed lane
    recorded: dict[str, str] = {}    # consumer-side lane: id -> summary
    probe_cards: list[dict] = []

    def card(probe: str, outcome: str, file: str = "", remediation: str = "") -> None:
        probe_cards.append({"probe": probe, "outcome": outcome,
                            "file": file, "remediation": remediation})

    # consumers must exist in live mode (clear error, never a silent shrink)
    consumers: dict[str, Path] = {}
    for p in consumer_paths:
        if p.is_dir():
            consumers[p.name] = p
        else:
            karta_failures.append(f"enrolled consumer directory missing: {p}")
            card("consumers", "fail", str(p),
                 "restore the sibling checkout or pass --consumers")

    # rule-hash guard
    ok, detail = match_pins.check_rule(target)
    card("matching-rule-hash", "pass" if ok else "fail", str(match_pins.SKILL_MD),
         "" if ok else match_pins.RULE_MISMATCH_MSG)
    if not ok:
        karta_failures.append(f"matching-rule hash: {detail}")

    # PROBE 1 — pack integrity
    builtin_dir = target / "skills" / "_shared" / "sme"
    karta_overlay = target / ".karta" / "sme"
    karta_packs = [p for p in sorted(builtin_dir.glob("*.md")) if p.name not in exceptions]
    if karta_overlay.is_dir():
        karta_packs += [p for p in sorted(karta_overlay.glob("*.md"))
                        if p.name not in exceptions]
    invalid, karta_warns, crash = run_validator(target, karta_packs)
    if crash:
        karta_failures.append(crash)
    karta_failures += [f"pack INVALID after exceptions: {f} ({'; '.join(errs[:2])})"
                       for f, errs in invalid.items()]
    card("pack-integrity/validate-packs-karta", "fail" if (invalid or crash) else "pass",
         next(iter(invalid), ""), "fix the pack or add a justified exceptions.json entry")

    consumer_warns: list[str] = []
    for name, root in consumers.items():
        files = [p for p in sorted((root / ".karta" / "sme").glob("*.md"))
                 if p.name not in exceptions]
        inv, warns, crash = run_validator(target, files)
        if crash:
            karta_failures.append(crash)
        consumer_warns += warns
        for f, errs in inv.items():
            recorded[f"pack-invalid:{name}:{Path(f).name}"] = \
                f"{f}: INVALID ({'; '.join(errs[:2])})"
        card(f"pack-integrity/validate-packs-{name}",
             "findings" if inv else "pass", next(iter(inv), ""), "")

    try:
        proc = subprocess.run([sys.executable, str(target / SHARED_COPIES)],
                              capture_output=True, text=True,
                              timeout=CHECK_TIMEOUT_S, cwd=str(target))
        mirror_ok = proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        mirror_ok = False
    if not mirror_ok:
        karta_failures.append("mirror drift: check_shared_copies.py nonzero")
    card("pack-integrity/mirror-parity", "pass" if mirror_ok else "fail", "",
         "" if mirror_ok else "re-run the sync writers for skills/_shared")

    vp = cross_prefix._load_validator(target)
    prefix_files = cross_prefix.collect(target, [karta_overlay] + [
        r / ".karta" / "sme" for r in consumers.values()])
    prefix_findings = cross_prefix.scan(prefix_files, vp)
    karta_failures += [f"cross-prefix: {f}" for f in prefix_findings]
    card("pack-integrity/cross-prefix", "fail" if prefix_findings else "pass", "",
         "; ".join(prefix_findings[:2]))

    # PROBE 2 — pin derivation table
    tags = load_release_tags(target)
    current_era = tags[-1][0] if tags else "untagged"
    pin_table: list[dict] = []
    smeless: list[str] = []
    era_counts: dict[str, int] = {}
    unmatched: dict[str, list[str]] = {}
    consumer_shas: dict[str, str] = {}
    corpus: set[str] = set()
    for name, root in consumers.items():
        consumer_shas[name] = _git(root, "rev-parse", "HEAD") or "unknown"
        contract_stack = match_pins.detect(root, target)
        coverage_stack, cov_dirs = match_pins.coverage_detect(root, target)
        corpus |= match_pins.corpus_of(contract_stack) | match_pins.corpus_of(coverage_stack)
        packs = match_pins.enumerate_packs(builtin_dir, root / ".karta" / "sme")
        expected_contract = match_pins.match_pins(packs, contract_stack)
        expected_coverage = match_pins.match_pins(packs, coverage_stack)
        all_tokens = {t.lower() for p in packs.values() if p["kind"] != "disabled"
                      for t in p["tokens"]}
        unmatched[name] = sorted(l for l in coverage_stack["languages"]
                                 if l.lower() not in all_tokens)
        binder_dir = root / ".karta" / "binders"
        binder_files = sorted(binder_dir.glob("*.json")) + \
            sorted((binder_dir / "archive").glob("*.json"))
        for bf in binder_files:
            label = f"{name}/{bf.stem}"
            try:
                sme = json.loads(bf.read_text()).get("sme")
            except (json.JSONDecodeError, OSError) as e:
                recorded[f"binder-unparseable:{name}:{bf.name}"] = f"{bf}: {e}"
                continue
            if not sme:
                smeless.append(label)  # excluded from the denominator, never failing
                continue
            date_s = _git(root, "log", "-1", "--format=%cI", "--",
                          str(bf.relative_to(root)))
            bdate = datetime.datetime.fromisoformat(date_s) if date_s else None
            era = era_of(bdate, tags)
            era_counts[era] = era_counts.get(era, 0) + 1
            pinned = sorted(sme)
            row = {"binder": label, "era": era, "pinned": pinned,
                   "expected_contract": expected_contract,
                   "expected_coverage": expected_coverage,
                   "underivable_pins": sorted(set(pinned) - set(expected_contract)),
                   "missing_pins": sorted(set(expected_contract) - set(pinned))}
            pin_table.append(row)
            if row["underivable_pins"] or row["missing_pins"]:
                recorded[f"pin-nonconformant:{name}:{bf.stem}"] = (
                    f"{label} [{era}]: underivable={row['underivable_pins']} "
                    f"missing={row['missing_pins']}")
        card(f"pin-derivation/{name}", "recorded", "",
             f"coverage dirs: {', '.join(cov_dirs)}")
    current_rows = [r for r in pin_table if r["era"] == current_era]
    conformant = [r for r in current_rows
                  if not r["underivable_pins"] and not r["missing_pins"]]
    conformance_pct = (round(100 * len(conformant) / len(current_rows), 1)
                       if current_rows else None)

    # PROBE 3 — seed drift + token audit (overlay packs are the audit scope)
    overlay_dirs = {"karta": karta_overlay} | {
        n: r / ".karta" / "sme" for n, r in consumers.items()}
    drift_rows = seed_drift.drift_report(target, overlay_dirs)
    for row in drift_rows:
        if row["class"] != "IDENTICAL":
            recorded[f"seed-drift:{row['owner']}:{row['pack']}:{row['class']}"] = (
                f"overlay {row['owner']}/{row['pack']} is {row['class']} vs built-in history")
    card("seed-drift", "recorded", "",
         f"{sum(1 for r in drift_rows if r['class'] != 'IDENTICAL')} non-identical")
    overlay_files = {o: [p for p in sorted(d.glob("*.md"))
                         if p.name not in match_pins.NOT_A_PACK]
                     for o, d in overlay_dirs.items() if d.is_dir()}
    for f in seed_drift.token_audit(overlay_files, corpus):
        recorded[f"{f['kind']}:{f['owner']}:{f['pack']}:{f['token']}"] = (
            f"{f['kind']} '{f['token']}' in {f['owner']}/{f['pack']}")
    card("token-audit", "recorded", "", f"corpus size: {len(corpus)}")

    # baseline + status
    tracked = _git(target, "ls-files", str(RESULTS_DIR)).splitlines()
    baseline_name = pick_baseline(tracked)
    baseline_ids: set[str] | None = None
    if baseline_name:
        blob = _git(target, "show", f"HEAD:{baseline_name}")
        try:
            baseline_ids = set(json.loads(blob)["recorded_finding_ids"])
        except (json.JSONDecodeError, KeyError, TypeError):
            karta_failures.append(f"baseline unreadable: {baseline_name}")
    status, detail_ids = decide_status(karta_failures, set(recorded), baseline_ids)

    findings = [{"finding_id": f"karta-owned-{i}", "severity": "error", "summary": s}
                for i, s in enumerate(karta_failures, 1)]
    if baseline_ids is None and detail_ids:
        findings += [{"finding_id": f"seeded-finding-absent:{s}", "severity": "error",
                      "summary": f"seeded finding missing from first baseline: {s}"}
                     for s in detail_ids]
    elif detail_ids:
        findings += [{"finding_id": f"regression:{s}", "severity": "error",
                      "summary": f"new finding vs committed baseline: {recorded.get(s, s)}"}
                     for s in detail_ids]
    findings += [{"finding_id": fid, "severity": "recorded", "summary": summary}
                 for fid, summary in sorted(recorded.items())]

    results = {
        "schema_version": 1,
        "vector": PROBE_ID,
        "run_date": run_date,
        "plugin_version": json.loads(
            (target / ".claude-plugin" / "plugin.json").read_text()).get("version", "unknown"),
        "karta_sha": _git(target, "rev-parse", "HEAD") or "unknown",
        "consumer_shas": consumer_shas,
        "pack_list": {"builtin": sorted(p.stem for p in builtin_dir.glob("*.md")
                                        if p.name not in exceptions)}
                     | {o: sorted(p.stem for p in files)
                        for o, files in overlay_files.items()},
        "probe_cards": probe_cards,
        "pin_table": pin_table,
        "binder_era_counts": era_counts,
        "smeless_excluded": sorted(smeless),
        "current_era": current_era,
        "conformance_pct_current_era": conformance_pct,
        "unmatched_ecosystems": unmatched,
        "drift": drift_rows,
        "token_audit_scope": "overlay packs (consumer .karta/sme + karta .karta/sme)",
        "token_corpus": sorted(corpus),
        "size_warnings": {"karta": karta_warns, "consumers": consumer_warns},
        "karta_owned_failures": karta_failures,
        "recorded_finding_ids": sorted(recorded),
        "baseline_file": baseline_name,
        "status": status,
    }
    out_path = target / RESULTS_DIR / f"{run_date}-sme-pack-static-suite.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2) + "\n")

    return {
        "id": PROBE_ID,
        "status": status,
        "partial": False,
        "implemented_checks": IMPLEMENTED_CHECKS,
        "findings": findings,
        "metrics": {
            "packs_checked": len(karta_packs) + sum(
                len(f) for o, f in overlay_files.items() if o != "karta"),
            "karta_owned_failures": len(karta_failures),
            "size_warnings": len(karta_warns) + len(consumer_warns),
            "binders_pinned": len(pin_table),
            "binders_smeless_excluded": len(smeless),
            "current_era": current_era,
            "conformance_pct_current_era": conformance_pct,
            "drift_non_identical": sum(1 for r in drift_rows if r["class"] != "IDENTICAL"),
            "recorded_findings": len(recorded),
            "baseline": baseline_name or "none (bootstrap)",
            "results_file": str(out_path.relative_to(target)),
        },
    }


# --- self-test -----------------------------------------------------------------

def _run_self_test() -> int:
    results: list[bool] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail and not ok else ''}")

    seeded = set(SEEDED_IDS) | {"pin-nonconformant:parchmark:backend-hygiene"}

    st, _ = decide_status(["mirror drift: check_shared_copies.py nonzero"], seeded, None)
    check("injected mirror drift flips status to fail", st == "fail")
    st, _ = decide_status(["cross-prefix: prefix collision: 'min' claimed by a, b"],
                          seeded, set(seeded))
    check("injected prefix collision flips status to fail", st == "fail")
    st, missing = decide_status([], seeded, None)
    check("bootstrap with all seeded findings present passes", st == "pass", repr(missing))
    st, missing = decide_status([], seeded - {"dead-token:gringotts:go-htmx:templ"}, None)
    check("bootstrap missing a seeded finding fails closed (probe blind to seeded truth)",
          st == "fail" and missing == ["dead-token:gringotts:go-htmx:templ"])
    st, _ = decide_status([], seeded, set(seeded))
    check("recorded findings equal to the committed baseline pass", st == "pass")
    st, new = decide_status([], seeded | {"seed-drift:parchmark:vue:DIVERGENT"}, set(seeded))
    check("a new finding vs the committed baseline is a regression (fail)",
          st == "fail" and new == ["seed-drift:parchmark:vue:DIVERGENT"])
    st, _ = decide_status([], seeded - {"overbroad-token:gringotts:go-htmx:go"}, set(seeded))
    check("a resolved finding is not a regression (only NEW ids fail)", st == "pass")

    picked = pick_baseline(["benchmarks/sme/results/2026-07-06-date-picker.md",
                            "benchmarks/sme/results/2026-07-18-sme-pack-static-suite.json",
                            "benchmarks/sme/results/2026-07-20-sme-pack-static-suite.json",
                            "benchmarks/sme/results/notes.txt"])
    check("baseline selection picks the newest tracked results file for this vector",
          picked == "benchmarks/sme/results/2026-07-20-sme-pack-static-suite.json",
          repr(picked))
    check("no tracked results file for this vector means bootstrap (None)",
          pick_baseline(["benchmarks/sme/results/2026-07-06-date-picker.md"]) is None)

    tz = datetime.timezone.utc
    tags = [("v1.16.0", datetime.datetime(2026, 7, 6, tzinfo=tz)),
            ("v1.17.0", datetime.datetime(2026, 7, 8, tzinfo=tz))]
    check("era partitioning: binder date between tags gets the older era",
          era_of(datetime.datetime(2026, 7, 7, tzinfo=tz), tags) == "v1.16.0")
    check("era partitioning: binder before all tags is pre-first-tag",
          era_of(datetime.datetime(2026, 1, 1, tzinfo=tz), tags) == "pre-v1.16.0")
    check("era partitioning: untracked binder counts as the current era",
          era_of(None, tags) == "v1.17.0")

    invalid, warns = parse_validator_output(
        "a.md: OK\nb.md: warning: pack is 4000 bytes\nb.md: OK\n"
        "c.md: INVALID\n  - missing 'name'\n  - missing 'description'\n"
        "d.md: unreadable (boom)\n")
    check("validator output parse: INVALID files with errors, warnings separate",
          invalid == {"c.md": ["missing 'name'", "missing 'description'"],
                      "d.md": ["d.md: unreadable (boom)"]}
          and warns == ["b.md: warning: pack is 4000 bytes"], repr((invalid, warns)))

    failures = results.count(False)
    total = len(results)
    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent,
                    help="karta repo root (default: this probe's repo)")
    ap.add_argument("--consumers", default=None, metavar="PATH,PATH",
                    help="consumer repo roots (default: consumers.json names as "
                         "sibling directories of the --target repo's parent)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    target = args.target.resolve()
    if args.consumers:
        consumer_paths = [Path(s).resolve() for s in args.consumers.split(",") if s]
    else:
        names = json.loads((target / "benchmarks" / "sme-static" /
                            "consumers.json").read_text())["consumers"]
        consumer_paths = [target.parent / n for n in names]
    print(json.dumps(live(target, consumer_paths,
                          datetime.date.today().isoformat()), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
