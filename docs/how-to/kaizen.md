# Kaizen: the stack-pack writer

Kaizen is karta's second writer, after doc-gardner. Doc-gardner keeps your prose docs matching your code; kaizen keeps your stack packs matching what your project has learned. When it is on, every `karta-deliver` run ends with a kaizen pass over your packs, and every change kaizen makes is a normal commit you review before you merge. It is off until you turn it on.

This guide covers what exists today — phase one, the frame: the on/off switch, the one-time seeding of your packs, and the review-by-commit loop. The behaviors that act on what your builds keep repeating arrive in later phases (see "What's coming" below).

## Turn it on

Add `.karta/kaizen.json` to your repo:

```json
{ "enabled": true }
```

Optionally add a plain nudge about what to watch (it is **not** a task list and never limits what kaizen may look at):

```json
{ "enabled": true, "focus": "watch the billing items and the auth rules" }
```

Remove the file, or set `"enabled": false`, to turn it off. **Off means kaizen never runs — even when you invoke the `karta-kaizen` skill directly.** This is stricter than doc-gardner on purpose: doc-gardner's switch governs only its automatic delivery path, and a standalone doc-gardner run works regardless; kaizen has no such carve-out. The file's shape is defined by `skills/karta-kaizen/references/kaizen-schema.json`, which ships with the karta-kaizen skill. karta's plugin validator gates only karta's own committed copy of this file — it never sees the one in your repo. To check yours, run this from your repo root:

```bash
python3 -c "import json; d=json.load(open('.karta/kaizen.json')); assert type(d.get('enabled')) is bool, 'enabled must be true or false'; assert set(d) <= {'enabled', 'focus'}, 'unknown keys (allowed: enabled, focus)'; assert d.get('focus') is None or type(d.get('focus')) is str, 'focus must be a string'; print('kaizen.json OK')"
```

With the switch on, kaizen runs after the doc-gardner phase of every delivery, and the delivery report carries its outcome next to doc-gardner's.

## The first run seeds your packs

A stack pack is a short markdown file of guidance for one technology or one part of your domain — do's, don'ts, and a review checklist karta checks builds against. karta ships built-in packs and applies the ones that match your stack.

The first time kaizen runs with the switch on, it copies every pack your project uses into `.karta/sme/` as a full, complete file. "Uses" is precise: after a delivery, that is the packs the binder pinned; on a direct "run kaizen", it is the packs matched to your detected stack. From then on, **those files are the packs**: the rules that apply to your builds are readable in one place, and you and kaizen edit those files directly — no hidden merge, no base-plus-override. The built-in packs become templates. They seed the copies, and they still cover any pack name your repo doesn't carry.

A pack you already put in `.karta/sme/` always wins — seeding never overwrites your own copy.

The one cost: once your repo owns a pack, it stops picking up changes to karta's built-in version of it. That is the trade for "what you read is what runs."

## The provenance stamp and refreshing stale copies

When kaizen seeds a pack, it writes a small provenance stamp into the copy: two paired frontmatter keys, `seeded_from` (the built-in the copy came from) and `base_sha256` (a fingerprint of that built-in at seed time). The stamp lets karta tell an out-of-date copy apart from one you have deliberately edited.

The first time kaizen runs with the switch on, it also does an eager migrate pass over the packs already in `.karta/sme/`. It classifies every file, stamps each copy that still matches its built-in, and refreshes each out-of-date copy in place — an `auto-reseed` that replaces the copy with the current built-in plus a fresh stamp. Every action prints one visible logged line, so you can see exactly what changed. The pass is naturally idempotent: a stamped, current copy classifies as clean on the next run, so re-running does nothing.

A copy only refreshes itself when its stamp names a hash the built-in genuinely shipped with — checked against a shipped ledger of past built-in hashes — so a forged stamp can never trick kaizen into overwriting a real local edit. A copy that carries a genuine edit of your own is left exactly as it is and reported, never overwritten: kaizen does not destroy your work.

## Review by commit

Kaizen never opens a PR and never pushes. In a delivery, every change it makes lands as a labeled `kaizen:` commit on the integration branch — the branch you already review and merge yourself. Those commits are your review surface:

- Inspect one: `git show` the `kaizen:` commit to see exactly what changed.
- Disagree with one: `git revert <sha>` like any commit, or drop it before you merge.

Invoke the skill directly (with the switch on) and it leaves the pack edits in your working tree instead, for you to review and commit yourself.

## The core rule

**Kaizen writes knowledge; it never changes what gates a build.** It writes only inside `.karta/sme/` and its own config area — never your code, tests, the binder, prose docs, or karta's built-in packs. It never loosens or removes a rule. Changing what blocks a build is your decision, made in review of kaizen's commits. Nothing kaizen does can quietly make your checks weaker.

## Plain language to you, precision in packs

What kaizen says to a person — run summaries, commit messages — follows karta's bundled `karta-plainlanguage` standard, so it reads clearly whatever your setup. What goes inside a pack stays technical: a pack is a precision artifact for the builder and the checker, and simplifying it would blunt it.

## What's coming

Phase one is the frame only. Later phases add:

- **Rule sharpening** — reading repeated `KARTA-SME-OVERRIDE` markers and tightening the pack where the fix is safe: a narrow exception, clearer wording, a better example.
- **Erosion notes** — when a rule keeps getting overridden, a plain note showing the pattern, the reasons given, and what loosening would let through — so you decide with your eyes open.
- **New-pack suggestions** — spotting a gap and drafting a new pack, plus the advisory mechanics that let it guide builders without gating builds until you promote it.

None of that runs today. Today kaizen seeds your packs and lands its edits as commits you review — and it does not pretend to more.

For the canonical agent and the generate-and-guard workflow, see [AGENTS.md](../../AGENTS.md).
