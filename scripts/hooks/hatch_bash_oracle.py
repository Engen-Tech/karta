# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Real-bash oracle for the commit gate's escape hatch (repo tooling, needs Docker).

`precommit_gate.hatch_prefixed()` decides, by reading command text, whether
KARTA_SKIP_GATE=1 is set on every commit a Bash command line can run. Reviewers
reasoning about bash missed most of its mistakes; real bash did not. This runs
every command through bash 5.2 in a container whose only `git` is a fake that
logs, for each commit that actually executes, whether the hatch reached it —
then compares that with the parser's verdict.

The corpus: the self-test truth table (lifted from the source, never copied),
the constructs below, and generated combinations of prefixes, bodies and
operators. It reports

  FALSE GRANT   the parser skips the gates, yet bash ran a commit without the
                hatch. Fails the run unless the command is in KNOWN_GRANTS.
  TABLE WRONG   the self-test expects a grant, yet bash ran an unprefixed
                commit — the test itself is wrong. Always fails the run.
  EVERYDAY SHAPE DENIED
                the parser denies one of MUST_GRANT, the shapes agents type daily.
                Always fails the run: on Windows, where the gates cannot pass,
                that is a wedge.
  FALSE DENY    every commit bash ran carried the hatch, yet the parser denied.
                Reported, never failing: a false deny only means the gates run,
                and many are `||` short-circuits no static parser can see.
  DETECTION GAP bash committed and the detector never fired. Reported only.

Not part of the commit gate: it needs Docker and takes minutes. Run it after
any change to the hatch parser, and ship the change only with zero failures.

  uv run --script scripts/hooks/hatch_bash_oracle.py [--generated N] [--seed S]
"""
from __future__ import annotations
import argparse, ast, os, random, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
IMAGE = "debian:bookworm-slim"   # bash 5.2 + coreutils, and no git of its own
sys.path.insert(0, str(HERE))
import precommit_gate as g  # noqa: E402

H, V = "KARTA_SKIP_GATE=1", "git commit"

# Deliberate spellings, pinned in precommit_gate's self-test as known gaps.
# Real bash runs the listed commit unprefixed; nobody types these by accident.
KNOWN_GRANTS = {
    f"{H} sh -c 'unset KARTA_SKIP_GATE; {V} -m y'",
    f"{H} {V} -m a; x='$({V} -m y)'; echo ${{x@P}}",
    f"{H} {V} -m a; git {{commit,}} -m y",
    f"{H} {V} -m a; $'\\x67it' commit -m y",   # ANSI-C escape spelling of `git`
}

# Shapes agents type every day. A false deny here is a wedge on Windows, where
# the gate suite cannot pass — so unlike other false denies, these fail the run.
MUST_GRANT = [
    f"{H} {V} -F msg.txt && git push",
    f'{H} {V} -q -F "C:/Users/Dev/AppData/Local/Temp/msg.txt"',
    f"cd repo && {H} {V} -m x",
    f'{H} {V} -am "x" 2>&1 | tail -5',
    f'git add -A && {H} {V} -m "x" && git log --oneline -1',
    f"{H} {V} -m \"$(cat <<'EOF'\nfix: the thing\n\nCo-Authored-By: X <a@b.c>\nEOF\n)\"",
    f"{H} {V} --amend --no-edit",
    f"{H} git -C /c/repo commit -m x",
    f"{H} {V} -m x; git status -sb",
    f'{H} {V} -m "fix: don\'t break (things)"',
    f'{H} {V} -m "a" -m "b > c | d && e"',
    f"{H} {V} -m x > /dev/null 2>&1 && echo ok",
    f"git stash && {H} {V} -m x && git stash pop",
    f"{H} {V} -m x && uv run scripts/check.py --self-test 2>&1 | tail -3",
    f"{H} {V} -m x && git push origin main v2.38.0",
    f"{H} {V} -F- <<'EOF'\nsubject\n\nbody with $HOME and `ticks`\nEOF",
    f'{H} {V} -m "x" && git log --format="%h %s" -1',
    f'{H} {V} -m x && grep -rn "{V}" docs/ | head',
    f'{H} {V} --trailer "Co-Authored-By: X <a@b>" -m x',
    f'{H} grep -n "{V}" notes.md | tail -5',
    # review round 5
    f'{H} grep -n "{V}" f.py > grep.log && git push',
    f'{H} grep -n "{V}" f.py | tee grep.log && git push',
    f"{H} {V} -F- <<'EOF' && git push\nfix: the {V} hatch\nEOF",
    f"{H} {V} -F- <<'EOF' | tail -2\nfix: the {V} hatch\nEOF",
    # review round 7, question B: an unrelated $VAR after a written file
    f'{H} grep -n "{V}" f > hits.txt && uv run tool.py --out "$OUT"',
    f'{H} grep -n "{V}" f > hits.txt && echo "done in $SECONDS s"',
    f'{H} grep -n "{V}" f > hits.txt && uv run tool.py --out "${{OUT}}"',
    # review round 10
    f'git diff "${{BASE:-HEAD}}"; {H} grep -n "{V}" f.py > hits.txt; git diff --stat',
    # review round 11
    f'git diff "${{BASE:-main}}" HEAD; {H} grep -n "{V}" f.py > hits.txt; git diff --stat',
    f'echo "${{A:-x}}" "${{B:-y}}"; {H} grep -n "{V}" f.py > hits.txt; git push',
    f'cp "${{SRC:-./in.txt}}" out.txt; {H} grep -n "{V}" f.py > hits.txt; git push',
    f'BASE=$(git merge-base HEAD main); git diff "$BASE" HEAD; {H} grep -n "{V}" f.py > hits.txt; git diff --stat',
]

# Constructs a generator would not produce: every shape raised in review, plus
# the shell features the parser has to model.
CONSTRUCTS = [
    f"{H} true & {V} -m x", f"{H} {V} -m x & {V} -m y", f"{H} {V} -m x |& {V} -m y",
    f"{H} echo $({V} -m x)", f"{H} {V} -m x $({V} -m y)", f"{H} {V} -m x `{V} -m y`",
    f"{H} {V} -m a && echo $({V} -m b)", f'{H} echo "{V} -m x" | bash',
    f"{H} sh -c 'unset KARTA_SKIP_GATE; {V} -m y'", f"env -- {H} {V} -m x",
    f"time {H} {V} -m x", f"{H} env {V} -m x", f"env {H} {V} -m x", f"env -i {H} {V} -m x",
    f"({H} {V} -m x)", f"{H} ( cd . && {V} -m x )", f'KARTA_"SKIP_GATE"=1 {V} -m x',
    f"{H} ./git commit -m x", f"{H} /usr/bin/git commit -m x", f"{H} notgit commit -m x",
    f'{H} echo "{V} -m x" | tee >(bash)', f"{H} diff <({V} -m x) f",
    f'{H} echo "{V} -m x" > >(bash)', f"KARTA_SKIP_GATE=$'1' {V} -m x",
    f"{H} {V} -m x 2>&1", f"{H} {V} -m x &> log", f"{{ {H} {V} -m x; }}",
    f"{H} {V} -F - <<EOF\nmsg that mentions {V}\nEOF", f"{H} bash <<EOF\n{V} -m y\nEOF",
    f'{H} {V} -m x && echo "$(echo a #)\n{V} -m y)"',
    f"{H} ({H[:-1]} {V} -m x)", f'env "--" {H} {V} -m x', f'{H} grep "{V}" f.py && git diff',
    f"{H} cat < <({V} -m x)", f"{H} {V} -m x && cat <({V} -m y)",
    f'{H} {V} -m x && echo "$(cat <<X\n)\nX\n{V} -m y)"',
    f"{H} {V} -m \"$(cat <<'EOF'\nfix: the {V} hatch (again)\nEOF\n)\"",
    f"f(){{ {V} -m y; }}; f", f"f(){{ {V} -m y; }}; {H} f", f"{H} {V} -m x; f(){{ {V} -m y; }}; f",
    f"for i in 1; do {V} -m y; done", f"for i in 1; do {H} {V} -m y; done",
    f"if true; then {H} {V} -m y; fi", f"while false; do {V} -m y; done; {H} {V} -m x",
    f"case x in x) {V} -m y;; esac", f"[[ -n x ]] && {V} -m y", f"[[ -n x ]] && {H} {V} -m y",
    f'bash <<< "{V} -m y"', f'{H} bash <<< "{V} -m y"', f'{H} {V} -m x; bash <<< "{V} -m y"',
    f"exec {V} -m y", f"{H} exec {V} -m y", f"coproc {V} -m y", f"{H} coproc {V} -m y",
    f'{H} echo "{V} -m y" > go.sh && source go.sh', f'{H} echo "{V} -m y" > go.sh && . ./go.sh',
    f"x='{V} -m y'; eval \"$x\"", f"{H} {V} -m a; x='{V} -m y'; eval \"$x\"",
    f"{H} {V} -m a; x='{V} -m y'; $x", f"{H} {V} -m a; export X='{V} -m y'; bash -c \"$X\"",
    f"{H} {V} -m a; x='$({V} -m y)'; echo ${{x@P}}",
    f"{H} {V} -m a; trap '{V} -m y' EXIT", f"{H} {V} -m a; trap '{V} -m y' DEBUG; true",
    f"shopt -s expand_aliases\nalias c='{V} -m y'\n{H} true\nc",
    f"time -p {H} {V} -m x", f"! {H} {V} -m x",
    f'arr=({V}); "${{arr[@]}}" -m y', f"{H} {V} -m a && arr=(git commit); \"${{arr[@]}}\" -m y",
    f"git {{commit,}} -m y", f"{H} {V} -m a; git {{commit,}} -m y",
    # review round 4
    f'{H} {V} -m a && git -c user.name="foo bar" commit -m b',
    f'{H} {V} -m a && git -C "./sub dir" commit -m b',
    f'{H} echo "{V} -m y">go.sh && bash go.sh', f'{H} echo "{V} -m y" > "go.sh" && bash go.sh',
    f'{H} echo "{V} -m y" >& go.sh && bash go.sh', f'{H} echo "{V} -m y" >&go.sh && bash go.sh',
    f"{H} env -i PATH=\"$PATH\" {V} -m x", f"{H} env -i {V} -m x", f"{H} env -u KARTA_SKIP_GATE {V} -m x",
    f"{H} {V} -m x && ${{X_UNSET:-git\ncommit -m y}}", f"{H} {V} -m x && eval ${{X_UNSET:-git\ncommit -m y}}",
    f'{H} {V} -m x; {{ echo "{V} -m y"; }} > go.sh; bash go.sh',
    f'{H} {V} -m x; if true; then echo "{V} -m y"; fi > go.sh; bash go.sh',
    f"{H} bash -c 'echo \"{V} -m y\" > go.sh'; bash go.sh",
    f"{H} {V} -m a; x='{V} -m y'; y=$x; eval \"$y\"",
    f"{H} {V} -m a; $'\\x67it' commit -m y",
    # review round 5
    f"{H} {V} -F - <<EOF\n$(printf ')' ; {V} -m y)\nEOF",
    f"{H} {V} -F - <<EOF\n$(echo \"a)b\" ; {V} -m y)\nEOF",
    f"cat <<EOF | bash\n{V} -m y\nEOF", f"{H} cat <<EOF | bash\n{V} -m y\nEOF",
    f"cat <<EOF | {H} bash\n{V} -m y\nEOF",
    # a written file reached under another name
    f'{H} echo "{V} -m y" > go.sh && bash *.sh', f'{H} echo "{V} -m y" > go.sh && bash g?.sh',
    f'{H} echo "{V} -m y" > go.sh && bash $(ls *.sh)', f'{H} echo "{V} -m y" > go.sh && cat go.sh > run.sh && bash run.sh',
    f'{H} echo "{V} -m y" > go.sh && cp go.sh run.sh && bash run.sh', f'{H} echo "{V} -m y" > go.sh && cat go.sh | bash',
    f'f=go.sh; {H} echo "{V} -m y" > $f && bash $f', f'{H} echo "{V} -m y" > go.sh && for s in *.sh; do bash "$s"; done',
    f'{H} echo "{V} -m y" > go.sh; {H} echo "{V} -m z" > go2.sh; bash go2.sh',
    f"{H} {V} -F- <<'A' && cat <<'B' | bash\nmsg\nA\n{V} -m y\nB",
    # review round 6
    f'{H} {V} -F- <<EOF\n$(echo "\\" )" ; {V} -m y)\nEOF',
    f'{H} {V} -F - <<EOF\n$(echo "a\\"b)"; {V} -m y)\nEOF',
    f'{H} echo "{V} -m y" > f; cat f > g; bash g', f'{H} echo "{V} -m y" > go.sh; x=go.sh; bash "$x"',
    f'{H} echo "{V} -m y" > go.sh; f=go.sh; bash "$f"', f'{H} echo "{V} -m y" > go.sh && cp g*.sh x.sh && bash x.sh',
    f'{H} echo "{V} -m y" > go.sh; bash ./go.sh', f'{H} echo "{V} -m y" > go.sh && ln go.sh r.sh && bash r.sh',
    f'{H} echo "{V} -m y" > go.sh && cp go.sh go.sh.bak && bash go.sh.bak',
    f'{H} echo "{V} -m y" > go.sh && bash $(find . -name go.sh)',
    # review round 7: other ways to write the file
    f'{H} echo "{V} -m y" >> go.sh && bash go.sh', f'{H} echo "{V} -m y" | tee -a go.sh && bash go.sh',
    f'{H} echo "{V} -m y" | dd of=go.sh && bash go.sh', f'{H} cat <<< "{V} -m y" > go.sh && bash go.sh',
    f'exec 3>go.sh; {H} echo "{V} -m y" >&3; bash go.sh',
    f'exec 3>go.sh; {H} echo "{V} -m y" > /dev/fd/3; bash go.sh',
    f'ln -s go.sh link; {H} echo "{V} -m y" > link; bash go.sh',
    f'{H} awk \'BEGIN{{print "{V} -m y" > "go.sh"}}\' && bash go.sh',
    f'{H} printf "%s\\n" "{V} -m y" > go.sh && sed -i s/y/z/ go.sh && bash go.sh',
    # review round 7
    f'{H} echo "{V} -m y" > go.sh && f=out.txt; cat go.sh > $f; bash out.txt',
    f'OUT=out.txt; {H} echo "{V} -m y" > "$OUT" && bash out.txt',
    f'{H} echo "{V} -m y" > "$UNSET_OUT" && bash out.txt',
    f'exec 3>f; {H} echo "{V} -m y" >&3; exec 3>&-; bash f', f'ln -s f1 f2; {H} echo "{V} -m y" > f1; bash f2',
    f'ln -sf run.sh go.sh; {H} echo "{V} -m y" > go.sh; bash run.sh',
    f'{H} ln -s go.sh tf.sh && echo "{V} -m y" > go.sh && bash tf.sh',
    f'{H} exec 3>tf.sh; echo "{V} -m y" >&3; bash tf.sh',
    f'rm -f go.sh target.sh; ln -s target.sh go.sh; {H} echo "{V} -m y" > go.sh; bash target.sh',
    f'{H} echo "{V} -m y" > go.sh; x=go.sh; bash "$x"', f'{H} echo "{V} -m y" > go.sh; bash "${{PWD}}/go.sh"',
    # review round 8
    f'{H} echo "{V} -m y" > "my script.sh" && bash "my script.sh"',
    f'{H} echo "{V} -m y" > go.sh && bash "${{SCRIPT:-go.sh}}"', f'{H} echo "{V} -m y" > go.sh && bash ${{OUT:-go.sh}}',
    f'{H} echo "{V} -m y" > go.sh && bash "${{OUT:+go.sh}}"',
    f'script=go.sh; {H} echo "{V} -m y" > go.sh; bash "$script"; script=other.sh',
    f'{H} echo "{V} -m y" > go.sh; x=$(ls *.sh); bash "$x"', f'{H} echo "{V} -m y" > go.sh; (x=go.sh; bash "$x")',
    # review round 9
    f'L=$(pwd)/link; ln -s go.sh "$L"; {H} echo "{V} -m y" > link; bash go.sh',
    f"{H} {V} -m a; (x='{V} -m y'; eval \"$x\")",
    f'{H} echo "{V} -m y" > go.sh; X=go.sh; for x in $X; do bash "$x"; done',
    f'{H} echo "{V} -m y" > go.sh; x=go; x+=.sh; bash "$x"',
    f'echo go.sh > list.txt; read -r s < list.txt; {H} echo "{V} -m y" > go.sh; bash "$s"',
    # review round 10
    f'x=link; x+=; ln -s go.sh "$x"; {H} echo "{V} -m y" > go.sh; bash link',
    f'echo link > lst; x=link; read -r x < lst; ln -s go.sh "$x"; {H} echo "{V} -m y" > go.sh; bash link',
    f'{H} echo "{V} -m y" > notify.sh; PREFIX=/opt; PREFIX+=/myapp; cat "$PREFIX/data"',
    f'{H} echo "{V} -m y" > go.sh; x=go; x+=.sh; bash "$x"',
    # review round 11
    f'{H} echo "{V} -m y" > a.sh; for x in a b; do x+=.sh; bash "$x"; done',
    f'cp "${{SRC:-go.sh}}" out.sh; {H} echo "{V} -m y" > go.sh; bash out.sh',
]

PREFIXES = ["", H + " ", "KARTA_SKIP_GATE='1' ", "X=KARTA_SKIP_GATE=1 ", "KARTA_SKIP_GATE=10 ",
            "env " + H + " ", "FOO=bar " + H + " ", H + " env ", "'KARTA_SKIP_GATE'=1 ",
            H + " KARTA_SKIP_GATE=0 ", "KARTA_SKIP_GATE=1x "]
BODIES = [f"{V} -m x", "git -C . commit -m x", "./git commit -m x", "true",
          f'echo "{V} -m y"', f'grep -n "{V}" f.py', f'bash -c "{V} -m y"', f"sh -c '{V} -m y'",
          f"echo $({V} -m y)", f'echo "$({V} -m y)"', f'{V} -m "$(cat msg.txt)"',
          f'{V} -m "KARTA_SKIP_GATE=1"', f'eval "{V} -m y"', f"({V} -m x)",
          f"{V} -m x 2>&1", f'echo "{V} -m y" > go.sh', "bash go.sh", "tail -2", "head"]
OPERATORS = [" && ", "; ", " | ", " & ", " || ", " |& "]


def self_test_table() -> dict[str, bool]:
    """The `hatch` list inside precommit_gate's self-test, read from the source."""
    src = (HERE / "precommit_gate.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "hatch" for t in node.targets):
            return {cmd: want for cmd, want in ast.literal_eval(node.value)}
    sys.exit("could not find the `hatch` table in precommit_gate.py")


def generated(count: int, seed: int) -> list[str]:
    rng, out = random.Random(seed), set()
    while len(out) < count:
        parts = [rng.choice(PREFIXES) + rng.choice(BODIES) for _ in range(rng.choice((1, 2, 2, 3)))]
        cmd = parts[0]
        for p in parts[1:]:
            cmd += rng.choice(OPERATORS) + p
        out.add(cmd)
    return sorted(out)


def run_in_bash(cases: list[str]) -> dict[int, list[str]]:
    """{case index: the hatch value each executed commit saw} from real bash."""
    with tempfile.TemporaryDirectory(prefix="hatch_oracle_") as tmp:
        work = Path(tmp)
        # LF only, whatever the checkout did to line endings: bash reads a CR as
        # part of the last word on every line.
        runner = (HERE / "hatch_bash_oracle.sh").read_bytes().replace(b"\r\n", b"\n")
        (work / "runner.sh").write_bytes(runner)
        (work / "cases").mkdir()
        for i, cmd in enumerate(cases):
            (work / "cases" / f"{i:05d}.sh").write_bytes((cmd + "\nwait\n").encode("utf-8"))
        proc = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{work.as_posix()}:/work", IMAGE, "bash", "/work/runner.sh"],
            capture_output=True, text=True, timeout=7200,
            env=dict(os.environ, MSYS_NO_PATHCONV="1"),  # keep Git Bash off the -v path
        )
        if proc.returncode:
            sys.exit(f"docker run failed ({proc.returncode}): {proc.stderr[-2000:]}")
    seen: dict[int, list[str]] = {}
    for line in proc.stdout.splitlines():
        idx, _, log = line.partition("\t")
        seen[int(idx)] = [t.split("=", 1)[1] for t in log.split() if t.startswith("COMMIT=")]
    return seen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--generated", type=int, default=1500, help="generated combinations (default 1500)")
    ap.add_argument("--seed", type=int, default=20260918, help="generator seed (default 20260918)")
    ap.add_argument("--show", type=int, default=25, help="rows to print per category")
    args = ap.parse_args()
    if not shutil.which("docker"):
        print("hatch_bash_oracle: docker is not on PATH; this check needs it.", file=sys.stderr)
        return 2

    table = self_test_table()
    gen = generated(args.generated, args.seed)
    cases = list(dict.fromkeys([*table, *MUST_GRANT, *CONSTRUCTS, *gen]))
    bash = run_in_bash(cases)

    grants, pinned, table_wrong, denies, runtime, gaps, wedged = [], [], [], [], [], [], []
    for i, cmd in enumerate(cases):
        ran = bash.get(i)
        if ran is None:
            continue
        det, grant = g.is_commit_command(cmd), g.hatch_prefixed(cmd)
        unprefixed = any(v != "1" for v in ran)
        if det and grant and unprefixed:
            (pinned if cmd in KNOWN_GRANTS else grants).append((cmd, ran))
        if table.get(cmd) is True and unprefixed:
            table_wrong.append((cmd, ran))
        if det and not grant and ran and not unprefixed:
            (runtime if " || " in cmd else denies).append((cmd, ran))
        if not det and ran:
            gaps.append((cmd, ran))
        if cmd in MUST_GRANT and not grant:
            wedged.append((cmd, ran))

    print(f"real bash ran {len(bash)} of {len(cases)} commands "
          f"(self-test table {len(table)}, everyday {len(MUST_GRANT)}, "
          f"constructs {len(CONSTRUCTS)}, generated {len(gen)})")
    for title, rows, show in (
            ("FALSE GRANT — fails the run", grants, True),
            ("TABLE WRONG — fails the run", table_wrong, True),
            ("EVERYDAY SHAPE DENIED — fails the run (a wedge on Windows)", wedged, True),
            ("known grant, pinned as a deliberate spelling", pinned, True),
            ("false deny (gates run; reported only)", denies, True),
            ("false deny only because || skipped a commit at runtime", runtime, False),
            ("detection gap (reported only)", gaps, True)):
        print(f"\n== {title}: {len(rows)}")
        for cmd, ran in rows[:args.show] if show else []:
            print(f"   {cmd!r}  ran={ran}")
    missing_pins = KNOWN_GRANTS - {cmd for cmd, _ in pinned}
    for cmd in sorted(missing_pins):
        print(f"\nnote: pinned known grant no longer reproduces — remove it from KNOWN_GRANTS: {cmd!r}")
    failed = bool(grants or table_wrong or wedged or len(bash) != len(cases))
    print(f"\n{'FAIL' if failed else 'PASS'}: {len(grants)} unpinned false grant(s), "
          f"{len(table_wrong)} wrong self-test expectation(s), "
          f"{len(wedged)} everyday shape(s) denied")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
