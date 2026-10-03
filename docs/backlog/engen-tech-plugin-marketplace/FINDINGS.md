# Engen-Tech plugin marketplace: one release-artifact repo for karta, steward and later plugins

Date: 2026-10-03
Status: design agreed with the owner, not started; no code changed.
Backlog item: `docs/backlog/README.md` §31 "Engen-Tech plugin marketplace for karta, steward and later plugins"

## TL;DR

Replace per-repo, single-plugin marketplaces with one GitHub repo, `Engen-Tech/plugins` (marketplace `name`: `engen-tech`). It holds only each plugin's shipped files under `plugins/<name>/` plus the catalog files. Each product repo "releases" into it from Forgejo: a script clones the release tag, copies the shipped files, lifts the plugin's catalog entry, records a lock entry, validates, and pushes directly to GitHub. Product repos stay canonical on Forgejo, with their GitHub mirrors kept as offline backup. There is no Forgejo copy of the marketplace.

## Today

- Every product repo is its own marketplace, named after its one plugin.
  - karta (Claude): `.claude-plugin/marketplace.json`, name `karta`, entry `source: "./"`, `strict: true`, an explicit list of 10 skill paths, `metadata.version` 2.38.3.
  - karta (Codex): `.agents/plugins/marketplace.json`, name `karta-local`, `{"source":"local","path":"./plugins/karta"}`. That path is the generated projection, which holds `.codex-plugin/{hooks/,plugin.json}` and `skills/`.
  - karta (Copilot): `.github/plugin/plugin.json` (agents `./.github/agents/`, skills `./skills/`).
  - steward (github.com/TejGandham/steward): `.claude-plugin/marketplace.json`, name `steward`, source `./`, no version. Claude Code and Pi only; no Codex or Copilot manifests.
- This machine has separate marketplaces registered for karta, steward, etch, roundtable, stackhouse-skills, and stackhouse-common.
- Install ids: `karta@karta`, `karta@karta-local` (Codex), `steward@steward`.
- Costs:
  - One marketplace per product.
  - An install clones the whole product repo into the plugin cache: karta's docs, tests, benchmarks, binders, and `.karta/` roundtable records.
  - karta's origin is Forgejo (https://brahma.myth-gecko.ts.net:3000/stackhouse/karta). Its GitHub mirror (github.com/Engen-Tech/karta) can lag.

## Options considered

- **A. Pointer catalog (rejected).** Catalog entries point at product repos through remote sources. Rejected for three reasons.
  1. Remote-source vocabularies differ per harness. Claude Code takes `github`/`url`/`git-subdir`; Copilot takes `github` with `path`; Codex's `url`/`git-subdir` exist only in a merged PR, not on its docs page. That means per-harness catalog files with different shapes, one resting on undocumented behavior.
  2. Installs still clone whole product repos.
  3. A pinned sha not yet mirrored to GitHub fails to install.
- **B. Monorepo that moves product source into the marketplace (rejected).** This is the layout stackhouse-common already uses (`"source": "./exa-rest"`), but it moves product code out of its own repo.
- **C. Release-artifact marketplace (chosen).** The B layout, with the code written in the product repo and copied in at release.

## Harness facts (researched 2026-10-03)

### Claude Code

Sources: plugin marketplace reference, plugins reference, and plugin loading page on code.claude.com (official docs).

- Remote sources: `{"source":"github","repo":"owner/repo","ref":"...","sha":"..."}`, `{"source":"url","url":"https://...","ref":"...","sha":"..."}`, `{"source":"git-subdir","url":"https://...","path":"tools/formatter","ref":"...","sha":"..."}`; plus a relative path string.
- `strict: true` (default): the entry's component fields are appended to `plugin.json`, except `hooks`, whose matchers replace the manifest's per event. `strict: false` with any component field gives "Plugin <name> has conflicting manifests" and the plugin fails to load. `plugin.json` accepts a `skills` array.
- Version: "When `plugin.json` also sets `version`, `plugin.json` takes precedence and `claude plugin validate` warns." Resolution order: manifest version, then entry version, then source hash.
- Identity: the plugin id is `<name>@<marketplace>`. Skills and agents are namespaced by the manifest name (`karta:karta-kaizen`). Two marketplaces offering the same plugin name install as separate ids, and their component namespaces collide (not gated).
- Reserved marketplace names include npm, github, pip, uv, cargo, gh in any casing. `metadata.pluginRoot` lets bare names resolve under a directory.
- Not documented: whether a `skills/<dir>` with no SKILL.md is skipped or errors.

### Codex CLI (installed: codex-cli 0.155.1)

Documented (developers.openai.com/codex/plugins/build):

- Marketplace file `.agents/plugins/marketplace.json` (repo) or `~/.agents/plugins/marketplace.json`.
- `codex plugin marketplace add owner/repo [--ref main] [--sparse PATH]`. Quote: "Marketplace sources can be GitHub shorthand (owner/repo or owner/repo@ref), HTTP or HTTPS Git URLs, SSH Git URLs, or local marketplace root directories."
- Local source paths are `./`-prefixed and relative to the marketplace root. Source error text: "local plugin source path must start with `./`".

Source-only, not on the docs page:

- Codex also reads `.claude-plugin/marketplace.json` and `.cursor-plugin/marketplace.json` (openai/codex `core-plugins/src/marketplace.rs`).
- `url` and `git-subdir` plugin sources (openai/codex PR #18017).

Config key is `plugin@marketplace`, for example `[plugins."my-plugin@local-repo"]`, where the marketplace part is its top-level `name`.

### Copilot CLI (docs.github.com/en/copilot/reference/cli-plugin-reference)

- `copilot plugin marketplace add` takes owner/repo, owner/repo#ref, a URL, or a local path.
- It reads `.github/plugin/marketplace.json`. Quote: "Copilot CLI also looks for the marketplace.json file in the .claude-plugin/ directory."
- Entry source: a relative path string, or `{"source":"github","repo":"owner/repo","ref":"v1.0.0","path":"plugins/my-plugin"}`. Git-URL form field names were not seen.
- The marketplace `name` is its registration key.
- Each plugin dir needs `plugin.json` at its root; legacy locations `.plugin/` and `.claude-plugin/` are named.
- Unconfirmed: whether `.github/plugin/plugin.json` is found inside a marketplace subfolder. karta's `docs/how-to/copilot-cli.md:15` documents only the direct `copilot plugin install Engen-Tech/karta`.

### Pi (local source /mnt/agent-storage/vader/src/earendil-pi)

- No marketplace or catalog concept. Installs are per package: `pi install npm:@foo/bar@1.0.0`, `pi install git:github.com/user/repo@v1`, https and ssh URL forms, `-l` for project-local. A ref pins the package and skips `pi update` (`packages/coding-agent/docs/packages.md:23-26`, `README.md:390-399`).
- karta's `package.json` is `"private": true`.
- Pi is unchanged by this feature.

## Design

### Repo name

GitHub repo `Engen-Tech/plugins`. The name was free on 2026-10-03; the org held only `karta` (public) and `keel` (private).

- The org already carries the brand, so `engen-plugins` would repeat it.
- No "claude" in the name, because the repo serves Codex and Copilot too.
- It holds the plugin files, so `plugins` describes it.

Marketplace `name`: `engen-tech`. This is what users type after the `@` and the key Codex and Copilot register. It is kebab-case and not reserved.

### Layout

- `plugins/<name>/`: shipped files only, with the same internal layout as the product repo, so each harness finds its manifest where it does today (`.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `.github/plugin/plugin.json`).
- `.claude-plugin/marketplace.json`: Claude Code. Copilot falls back to it.
- `.agents/plugins/marketplace.json`: Codex, `{"source":"local","path":"./plugins/<name>"}`, today's documented shape.
- `.github/plugin/marketplace.json`: only if Copilot rejects Claude-only fields.
- A lock file (name TBD) recording, per plugin, the source repo URL, tag, commit sha, and a hash of the copied tree.
- `scripts/release.py` and `scripts/validate.py`.
- README with per-harness install lines, including Pi lines that point at the product repos.

| Harness | Catalog file | Plugin source |
|-|-|-|
| Claude Code | `.claude-plugin/marketplace.json` | `"./plugins/karta"` |
| Codex | `.agents/plugins/marketplace.json` | `{"source":"local","path":"./plugins/karta"}` |
| Copilot CLI | falls back to `.claude-plugin/marketplace.json` | relative path |
| Pi | none | `pi install git:...@tag` from the product repo |

Install commands after the move: `/plugin marketplace add Engen-Tech/plugins`, then `/plugin install karta@engen-tech`.

### Release flow

Runs as a Forgejo job. The script is owned by the marketplace repo and called from each product repo's release checklist, for example `release.py karta v2.38.4`.

1. Clone the tag from the product repo's origin (Forgejo for karta, so mirror lag does not matter).
2. Copy the shipped set into `plugins/<name>/`.
3. Lift the plugin's entry from the product repo's own `.claude-plugin/marketplace.json` (and Codex's) into the catalog, rewriting only `source`.
4. Write the lock entry.
5. Run the validator.
6. Push straight to `main` of `Engen-Tech/plugins` with a fine-grained GitHub token that can write contents to that one repo only, stored in Forgejo secrets.

### Drift guard

The validator rebuilds each lock entry from its source and fails on any difference, so a hand edit under `plugins/` cannot land. This applies the "identity is proven by content" invariant. Whoever implements the validator consults `docs/conventions/invariants.md` first.

### Pinning

The marketplace's `main` moves only on a release, so tracking `main` is reproducible. Entries carry no ref or sha.

### No Forgejo copy

Everything under `plugins/` is rebuildable from the lock file plus the product repos, and users install only from GitHub. A writable second copy plus a push mirror would bring back the mirror lag (karta's mirror has lagged before and needed a direct GitHub push). If an offline copy is wanted, add a read-only Forgejo pull mirror of GitHub. The `create-repo` skill sets up the opposite direction (Forgejo primary, GitHub push mirror), so the pull mirror is a one-off manual setup.

### Product repos

They stay canonical on Forgejo, and their GitHub mirrors stay as offline backup. Their own marketplace files stay, because release step 3 reads them. Each one changes its description to say "Install from Engen-Tech/plugins".

## Changes needed in this repo

Inventory taken 2026-10-03 at d076db0. Re-check line numbers before editing.

### Keeps working unchanged

The release lifts karta's entry verbatim and rewrites only `source` in the marketplace copy, so these checks stay valid.

- `scripts/validate_plugin.py:976-998`: the Claude marketplace entry must have `source == "./"`; a `skills` list must equal the `skills/` dirs both ways. The comment at :976-978 reads "a `strict` entry only loads what it lists", which is why the list is explicit; it dates from the first commit 217169f.
- `scripts/validate_plugin.py:1136-1165`: Codex marketplace name, interface.displayName, source.source/path, path exists, policy, category, and plugin name equal to the `.codex-plugin/plugin.json` name.
- `tests/pi/phase0-unit.test.ts:24-32`: marketplace `metadata.version` equals the `package.json` version. This is the only enforcement. No bump script exists; the version is hand-edited per `AGENTS.md:51` and `docs/releases/v1.14.0-checklist.md:14`.
- `benchmarks/claims.yaml:47-55` reads the marketplace description.
- `scripts/sync_codex_skills.py` and `scripts/sync_codex_agents.py` read or write neither marketplace file.

### Must change

- `README.md:35-36` (`/plugin marketplace add https://github.com/Engen-Tech/karta.git`, `/plugin install karta@karta`) and the `README.md:45` prose.
- `docs/how-to/claude-code.md:10-11` (same two commands).
- `docs/how-to/codex.md:18-19` (`codex plugin marketplace add https://github.com/Engen-Tech/karta.git`, `codex plugin add karta@karta-local`).
- `docs/how-to/copilot-cli.md:15` (`copilot plugin install Engen-Tech/karta`).
- `benchmarks/perf/fixture-v1/runner.sh:73` (`claude plugin install karta@karta --scope project --yes`). This is code and it breaks.
- Prose that goes stale: `benchmarks/perf/fixture-v1/README.md:74` and `docs/specs/2026-06-18-codex-compatibility-design.md:126`.
- `AGENTS.md:50-51` layout table (marketplace rows): note that the marketplace files are now the release descriptor.
- The release checklist gains the release-to-marketplace step, and the `.claude-plugin/marketplace.json` description gains the "Install from Engen-Tech/plugins" line.

Dated records stay as history: `docs/releases/v1.0.0-checklist.md:22` and `docs/showcase/codex-1.19-compatibility/*.md`.

Unchanged: the Pi install lines at `README.md:61` and `docs/how-to/pi.md:29,38,47,57`.

No hits anywhere in karta or steward for `known_marketplaces`, `enabledPlugins`, `marketplaces/karta`, or `marketplaces/steward`.

### Steward (github.com/TejGandham/steward)

`README.md:13-14` (`claude plugin marketplace add tejgandham/steward`, `claude plugin install steward@steward --scope user`), its marketplace description, and a release-checklist line. Steward appears only in the Claude catalog file. It can stay under TejGandham because the marketplace copies it.

## User migration

Install ids change from `karta@karta`, `karta@karta-local`, and `steward@steward` to `karta@engen-tech` and `steward@engen-tech`. Everyone reinstalls once. The Codex config key `[plugins."karta@karta-local"]` becomes `karta@engen-tech`. Skill and agent names (`karta:karta-kaizen`) come from the manifest name and do not change.

A user with both the old and new marketplaces registered gets two plugins named `karta` with colliding skills, so the migration note says to remove the old marketplace.

## First release must prove, not assume

1. **karta runs from the shipped set alone.** Today the plugin cache holds the whole repo, so nothing shows that a skill, script, or hook stays inside `skills/`, `agents/`, `hooks/`, and the manifests. Install from the marketplace into a scratch Claude Code profile (and Codex and Copilot) and run karta-plan, karta-deliver, and a hook before announcing it.
2. **Copilot finds `.github/plugin/plugin.json` inside a marketplace subfolder, and accepts Claude-only fields** (`strict`, `$schema`, `metadata`) in `.claude-plugin/marketplace.json`. If not, the release also writes `.github/plugin/marketplace.json`.
3. **Codex loads `./plugins/karta/.codex-plugin/plugin.json`** when the same folder also holds `.claude-plugin/` and Claude's `hooks/`.
4. **`claude plugin validate` passes** on the marketplace repo.

## Decisions made (2026-10-03)

- Release-artifact marketplace (option C) over a pointer catalog.
- GitHub `Engen-Tech/plugins`, marketplace name `engen-tech`.
- Product repos stay canonical on Forgejo, with GitHub mirrors as offline backup.
- A release pushes from Forgejo directly to `Engen-Tech/plugins`. The earlier alternative, a branch merged by a person, was set aside; the validator runs before the push instead.
- No Forgejo copy of the marketplace; an optional read-only pull mirror only.

## Open questions

- Where each product declares its shipped set, and what karta's set is. The leading option is an explicit include list in the product repo, checked by that repo's validator. Candidate for karta: `.claude-plugin/plugin.json`, the 10 listed `skills/` dirs, `agents/`, `hooks/` (`hooks.json` and `scripts/`), `.codex-plugin/`, `.github/plugin/plugin.json`, `.github/agents/`.
- Lock file name and format.
- Follow-up, out of scope: once the marketplace is live, karta could retire its `plugins/karta/` Codex projection and its own Codex marketplace. The projection exists only because Codex local sources must be a child path of the marketplace root, which `./plugins/karta` in the marketplace repo already is.
- Later plugins to onboard, one row each:

| Plugin | Source |
|-|-|
| etch | github.com/TejGandham/etch |
| roundtable | Forgejo stackhouse/roundtable |
| stackhouse-skills | github.com/TejGandham/stackhouse-skills |
| exa-rest | common-skills |
