# Stack packs: teaching karta your stack

A stack pack is a short markdown file of expert guidance for one technology or one part of your domain. karta reads the packs that match your project and holds every build to them: the advisory sections shape how code gets written, and the **Review checklist** is enforced — the safety-auditor judges each item's diff against it and kicks a miss back to build. This guide is for the maintainer writing or editing a pack.

## Where packs live

Built-in packs ship inside the plugin: `minimalism` (always on), `angular`, `vue`, `python`, `python-fastapi`, `go-naming`, and `go-htmx`, plus a `platform-native` reference they point at. Your project adds its own in `.karta/sme/*.md`. On a name clash the project file wins — drop a `.karta/sme/minimalism.md` in your repo and karta reads yours, not the built-in. (With [kaizen](kaizen.md) on, the first enabled run copies every pack your project uses into `.karta/sme/`; from then on those files are the packs, and this guide is how you edit them.)

A React/Next pack is deliberately missing: it is deferred until its rules have been validated against real projects. Write your own overlay pack if you need one now.

## The frontmatter

Every pack opens with a frontmatter block between two `---` lines:

```markdown
---
name: python
description: Generic Python do's and don'ts
match: ["python"]
see_also: ["platform-native#python-standard-library"]
---
```

- `name` — must equal the file's basename without `.md`. Required.
- `description` — one line saying what the pack covers. Required.
- Exactly one of:
  - `match: ["token", ...]` — the pack applies when a token matches your detected stack (next section), or
  - `always: true` — the pack applies to every binder, unconditionally.
- `see_also` — optional pointers to companion material; section anchors allowed.
- `disabled: true` — makes this a **suppression pack**: it is never pinned to a binder and exists only to switch a pack off. To suppress the built-in `minimalism` for your project, drop a `.karta/sme/minimalism.md` carrying just `name`, `description`, and `disabled: true`. A disabled pack needs no `match`/`always` and no checklist.

No other keys are allowed — the validator fails on anything it does not recognize.

## How matching works

`skills/karta-plan/scripts/detect_stack.py` scans your repo's manifests — `package.json`, `pyproject.toml`, `requirements*.txt`, `go.mod`, `Cargo.toml`, `Gemfile`, `composer.json` — and emits two lists: dependency names and languages (`python`, `javascript/node`, `go`, `rust`, `ruby`, `php`). A pack applies when one of its `match` tokens **equals** (case-insensitive, whole token) a name on either list. There is no substring or free-prose guessing: `match: ["fastapi"]` fires on the `fastapi` dependency and on nothing else. A pack you want everywhere uses `always: true` instead.

Matching sees only manifests, so a stack the manifests can't see never auto-matches. The canonical case is `go-htmx`: the pack itself mandates vendoring htmx as a static file, which leaves no manifest trace — a Go app with vendored htmx and stdlib templates matches only via a `github.com/a-h/templ` require or an `htmx.org` entry in `package.json`. To opt in anyway, copy the built-in to `.karta/sme/go-htmx.md` and replace its `match` line with `always: true` — your overlay wins by name (the same escape works for any manifest-invisible stack).

## The four sections — one has teeth

A pack's body is advisory guidance plus one enforced section. The advisory part is customarily **Do / Don't / Patterns** — the exact headings are yours. The enforced part is the `## Review checklist` section, and its heading is fixed.

**Warning: every line you add to the Review checklist becomes a gate.** The safety-auditor judges each built item's diff against every checklist rule of every applied pack; a miss that is neither fixed nor declared (see overrides below) is a VIOLATION that kicks the item back to build. Put a rule there only when you want builds blocked over it. Guidance you would merely like followed belongs in the advisory sections.

## Writing checklist rules

Each rule is one line in a fixed format:

```markdown
- [ ] py.1 — No bare `except:` — catch the narrowest exception the block can raise.
```

- The id is `<prefix>.<number>`. Each pack has one prefix, fixed for its lifetime: `min` (minimalism), `ng` (angular), `vue`, `py` (python), `fapi` (python-fastapi), `goname` (go-naming), `htmx` (go-htmx). A new pack registers its own short prefix — pick one and never change it.
- **Ids are never recycled.** A retired rule keeps its number forever as a tombstone line, so an override marker already sitting in someone's code never silently points at a different rule:

  ```markdown
  - ~~py.3~~ retired: superseded by py.5, which covers async too.
  ```

- **Every rule must be diff-checkable**: a reviewer holding nothing but the item's diff must be able to say pass or miss. "No new dependency where the stdlib already ships it" is checkable from the diff. "The service stays fast under load" is not — it needs a benchmark, a running system, history. If judging your rule takes anything beyond the diff, it does not belong in the checklist.

## Overriding a rule

Sometimes deviating is right. The builder leaves the code as-is and declares the deviation with an inline comment at the site:

```
KARTA-SME-OVERRIDE(min.1): requests is already a transitive dep and handles the retries we'd otherwise hand-roll. [ceiling: fine below 10 req/s; upgrade: move to httpx when we add async]
```

The `ceiling`/`upgrade` block is optional — name it when the shortcut is knowingly temporary; a permanent justified exception needs only the rationale. A declared override passes the safety-auditor, and `karta-debt` harvests every marker into a one-shot ledger so nothing rots unseen. An older marker form that names the pack and paraphrases the rule — `KARTA-SME-OVERRIDE(minimalism: no new dependency where stdlib suffices): ...` — is still recognized, but `karta-debt` flags it for migration to the rule-id form.

**Your repo's documented conventions outrank a built-in pack rule.** When your `CLAUDE.md` or `AGENTS.md` documents a convention that contradicts a built-in rule, the repo convention wins — declare the deviation with a marker citing where the convention lives, as `repo-rule: <path>:<line-or-section>`:

```
KARTA-SME-OVERRIDE(py.1): repo logging convention wraps handlers in a catch-all boundary — repo-rule: AGENTS.md:Error handling
```

## Validate before you commit

Run the bundled validator over any pack you write or edit:

```bash
python3 skills/karta-kaizen/scripts/validate_packs.py .karta/sme/*.md
```

It is stdlib-only and fails closed: the frontmatter shape (`name` equals the basename, only known keys, exactly one of `match`/`always` unless disabled), the required Review checklist section, the item format, and id discipline (right prefix, no duplicates, retired ids stay retired). It warns — without failing — when a pack grows past 3,500 bytes: packs are read on every matching build, so keep them short and sharp.

For the writer that evolves your packs from what your builds keep repeating, see [kaizen.md](kaizen.md).
