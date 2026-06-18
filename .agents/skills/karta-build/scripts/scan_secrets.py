# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""karta secret scan: block a commit when the staged diff carries a credential.

Scans the full staged diff (added and changed lines), applies one fixed
high-signal pattern set, and honors an in-repo allow-list. Stdlib only — it
does not assume grep/find/WSL exist; git is the one external tool, already
the backbone of the build/integration model.

Run it from the worktree or repo root: it reads the whole staged diff with
`git diff --cached`, not a directory-scoped subset, so a secret staged
anywhere in the worktree is seen regardless of where the scan starts.

Usage:
  uv run skills/karta-build/scripts/scan_secrets.py            # scan staged diff, exit 0/1
  uv run skills/karta-build/scripts/scan_secrets.py --allowlist .karta/secret-scan-allowlist
  uv run skills/karta-build/scripts/scan_secrets.py --self-test  # embedded fixtures, exit 0/1

Exit codes: 0 = clean (or every hit allow-listed), 1 = a secret hit blocks the commit.

This is a high-signal floor, not a full security scanner. It catches the
common accidental credential commit; it does not replace project secret
tooling (git-secrets, trufflehog, detect-secrets).
"""
from __future__ import annotations
import argparse, fnmatch, math, re, subprocess, sys
from pathlib import Path

# High-signal patterns. Sourced from the public rule sets of gitleaks, Yelp
# detect-secrets, and trufflehog — the subset chosen for low false positives
# (a distinctive prefix or an explicit key-shaped assignment), not breadth.
#
# openai-key requires length >= 40 (real keys are sk- + ~48 chars) and a
# negative lookahead for the sk-ant- form so an Anthropic key matches
# anthropic-key only — one key, one finding.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws-access-key-id", re.compile(r"(?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{16}")),
    ("aws-secret-access-key", re.compile(r"(?i)aws.{0,20}?(?:secret|private).{0,20}?['\"][0-9a-zA-Z/+=]{40}['\"]")),
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36}")),
    ("github-fine-grained-pat", re.compile(r"github_pat_[A-Za-z0-9_]{82}")),
    ("gitlab-pat", re.compile(r"glpat-[0-9A-Za-z_-]{20}")),
    ("slack-token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("slack-webhook", re.compile(r"https://hooks\.slack\.com/services/T[A-Za-z0-9_]{8}/B[A-Za-z0-9_]{8,12}/[A-Za-z0-9_]{24}")),
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----")),
    ("stripe-secret-key", re.compile(r"sk_live_[0-9A-Za-z]{24}")),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-key", re.compile(r"sk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{40,}")),
]

# Generic "name = high-entropy literal" catch. Only fires when the assigned
# string both looks key-shaped AND clears an entropy floor, to hold false
# positives down. The key-suffix names (secret_key, client_secret,
# private_key, access_key, api_key) are listed before bare `secret`/`token`
# so the most iconic names (e.g. Django/Flask SECRET_KEY) match the full
# variable, not a leading fragment.
SECRET_NAME = re.compile(
    r"(?i)(?:secret[_-]?key|client[_-]?secret|private[_-]?key|access[_-]?key|api[_-]?key"
    r"|secret|token|passwd|password)"
    r"\s*[:=]\s*['\"]([A-Za-z0-9/+=_-]{20,})['\"]"
)
ENTROPY_FLOOR = 4.0  # Shannon bits/char; gitleaks/detect-secrets use ~3.5–4.5


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def staged_added_lines() -> list[tuple[str, int, str]]:
    """Return (path, line_no, text) for every added line in the staged diff.

    Reads the full staged diff with `git diff --cached -U0` — no path scope,
    so a secret staged anywhere in the worktree is seen. No grep/find.
    Returns [] when git is absent or nothing is staged.
    """
    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--no-color", "-U0"],
            text=True, capture_output=True, check=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []
    rows: list[tuple[str, int, str]] = []
    path = ""
    new_line = 0
    hunk = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    for raw in out.splitlines():
        if raw.startswith("+++ b/"):
            path = raw[6:]
        elif raw.startswith("+++ "):
            path = raw[4:]
        elif raw.startswith("@@"):
            m = hunk.match(raw)
            new_line = int(m.group(1)) if m else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            rows.append((path, new_line, raw[1:]))
            new_line += 1
    return rows


def load_allowlist(path: Path) -> list[tuple[str, str]]:
    """Parse `path glob : pattern description` lines; blanks and # comments skipped."""
    entries: list[tuple[str, str]] = []
    if not path.exists():
        return entries
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        glob, _, desc = line.partition(":")
        entries.append((glob.strip(), desc.strip()))
    return entries


def is_allowed(file_path: str, pattern_name: str, allow: list[tuple[str, str]]) -> bool:
    for glob, desc in allow:
        if fnmatch.fnmatch(file_path, glob) and (not desc or desc == pattern_name or desc in pattern_name):
            return True
    return False


def scan_lines(rows: list[tuple[str, int, str]], allow: list[tuple[str, str]]) -> list[dict]:
    findings: list[dict] = []
    for path, line_no, text in rows:
        for name, pat in PATTERNS:
            if pat.search(text) and not is_allowed(path, name, allow):
                findings.append({"file": path, "line": line_no, "pattern": name})
        m = SECRET_NAME.search(text)
        if m and shannon_entropy(m.group(1)) >= ENTROPY_FLOOR:
            if not is_allowed(path, "high-entropy-assignment", allow):
                findings.append({"file": path, "line": line_no, "pattern": "high-entropy-assignment"})
    return findings


def _run_self_test() -> int:
    allow = [("tests/fixtures/**", "aws-access-key-id"), ("docs/examples/*.md", "github-token")]
    cases = [
        ("clean line", [("src/app.py", 1, "x = compute(value)")], []),
        ("aws key id", [("src/c.py", 3, 'k = "AKIAIOSFODNN7EXAMPLE"')], ["aws-access-key-id"]),
        ("github token", [("src/c.py", 4, "t = ghp_" + "a" * 36)], ["github-token"]),
        ("private key", [("k.pem", 1, "-----BEGIN RSA PRIVATE KEY-----")], ["private-key-block"]),
        ("slack token", [("c.py", 1, "x = xoxb-123456789012-abcdefghijkl")], ["slack-token"]),
        ("jwt", [("c.py", 1, "h = eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N")], ["jwt"]),
        ("entropy assign api_key", [("c.py", 1, 'api_key = "Zx9KpQ2mWf7Lr4Tv8Nd6Yb3Hc5Ja1Ge0"')], ["high-entropy-assignment"]),
        ("SECRET_KEY caught", [("settings.py", 1, 'SECRET_KEY = "Zx9KpQ2mWf7Lr4Tv8Nd6Yb3Hc5Ja1Ge0"')], ["high-entropy-assignment"]),
        ("secret_key caught", [("c.py", 1, 'secret_key = "Zx9KpQ2mWf7Lr4Tv8Nd6Yb3Hc5Ja1Ge0"')], ["high-entropy-assignment"]),
        ("client_secret caught", [("c.py", 1, 'client_secret = "Zx9KpQ2mWf7Lr4Tv8Nd6Yb3Hc5Ja1Ge0"')], ["high-entropy-assignment"]),
        ("low-entropy assign", [("c.py", 1, 'password = "aaaaaaaaaaaaaaaaaaaa"')], []),
        ("anthropic key one finding", [("c.py", 1, "k = sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")], ["anthropic-key"]),
        ("openai key needs length", [("c.py", 1, "k = sk-proj-short-slug-here-only")], []),
        ("openai key long", [("c.py", 1, "k = sk-" + "A" * 44)], ["openai-key"]),
        ("allow-listed aws", [("tests/fixtures/keys.py", 1, 'k = "AKIAIOSFODNN7EXAMPLE"')], []),
        ("allow-listed gh in docs", [("docs/examples/x.md", 1, "ghp_" + "b" * 36)], []),
    ]
    failures = 0
    for name, rows, expected in cases:
        got = sorted({f["pattern"] for f in scan_lines(rows, allow)})
        ok = got == sorted(set(expected))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {got}")
        failures += 0 if ok else 1
    # parser/diff helper: confirm path tracking and line numbering
    diff = (
        "+++ b/src/c.py\n"
        "@@ -0,0 +1,2 @@\n"
        '+secret = "ghp_' + "c" * 36 + '"\n'
        "+ok = 1\n"
    )
    parsed = []
    path, new_line = "", 0
    hunk = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    for raw in diff.splitlines():
        if raw.startswith("+++ b/"):
            path = raw[6:]
        elif raw.startswith("@@"):
            mm = hunk.match(raw)
            new_line = int(mm.group(1)) if mm else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            parsed.append((path, new_line, raw[1:]))
            new_line += 1
    ok = parsed and parsed[0] == ("src/c.py", 1, 'secret = "ghp_' + "c" * 36 + '"') and parsed[1][1] == 2
    print(f"[{'PASS' if ok else 'FAIL'}] diff parse: {parsed}")
    failures += 0 if ok else 1
    total = len(cases) + 1
    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Scan the staged diff for committed secrets.")
    ap.add_argument("--allowlist", type=Path, default=Path(".karta/secret-scan-allowlist"))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    allow = load_allowlist(args.allowlist)
    findings = scan_lines(staged_added_lines(), allow)
    if findings:
        print("SECRET SCAN: BLOCKED")
        for f in findings:
            print(f"  - {f['file']}:{f['line']}: {f['pattern']}")
        print("Remove or rotate the secret, or add a reviewed allow-list entry, before retry.")
        return 1
    print("SECRET SCAN: CLEAN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
