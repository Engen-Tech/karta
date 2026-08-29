export const meta = {
  name: 'binder-review-panel',
  description: 'Adversarial multi-lens review of a karta binder, with every finding verified against the repo before it counts',
  whenToUse: 'Before committing a binder (mode "binder", the default), or before landing a karta/<slug>/integration branch on the default branch (mode "delivery"). Pass args: {"binder": "<slug>", "mode": "binder"|"delivery", "branch": "<override>", "base": "<override>", "focus": "<optional extra lens>"}',
  phases: [
    { title: 'Read', detail: 'one pass establishes the ground truth every lens is held to' },
    { title: 'Review', detail: 'six adversarial lenses, run in waves' },
    { title: 'Verify', detail: 'each finding re-checked against the source before it counts' },
    { title: 'Report', detail: 'triage into blocking / surfaced / refuted' },
  ],
}

// ---------------------------------------------------------------------------
// This is karta's house review when the roundtable panel is unavailable.
//
// It is NOT a multi-model review and must never be recorded as one. Every lens
// here is the same model wearing a different hat, so it cannot satisfy the
// min_providers floor in .karta/roundtable.json, and scripts/roundtable/run_review.py
// must not be fed its output. Its verdict is advisory: a human reads it and decides.
//
// What it has over an external panel is access — every lens can open the actual
// source and run the actual commands, so a finding either cites a file:line or
// it does not survive the verify phase.
// ---------------------------------------------------------------------------

// Workflow scripts get no filesystem API, so the repo root cannot be derived here.
// Pass args.root to run this anywhere other than the default checkout.
const ROOT = (args && args.root) || '/mnt/agent-storage/vader/src/karta'
const SLUG = (args && args.binder) || 'watch-fidelity'
const EXTRA = (args && args.focus) || ''
const MODE = (args && args.mode) || 'binder'
const BINDER = `${ROOT}/.karta/binders/${SLUG}.json`

// Delivery mode reviews built code, not a plan. The binder is still the yardstick — it is what the
// delivery promised — but it has usually been archived by the time the branch is ready to land, so the
// lenses read it off the branch rather than from .karta/binders/.
const BRANCH = (args && args.branch) || `karta/${SLUG}/integration`
const BASE = (args && args.base) || 'main'
const RANGE = `${BASE}...${BRANCH}`

const TARGET = MODE === 'delivery' ? `
UNDER REVIEW: the delivered branch \`${BRANCH}\`, about to land on \`${BASE}\`. The diff is \`git diff ${RANGE}\`
and the commits are \`git log --oneline ${RANGE}\`. This is the last moment before this content reaches
the default branch, so a defect that survives here ships.

The binder is the yardstick — it is what this delivery promised. It has usually been archived by the
time the branch is ready, so find it with \`git show ${BRANCH}:.karta/binders/archive/${SLUG}.json\`,
falling back to \`git show ${BRANCH}:.karta/binders/${SLUG}.json\`.

GROUND RULE ON SOURCE: read the BRANCH, never the working tree and never ${BASE}. Every claim about what
the code does now must come from \`git show ${BRANCH}:<path>\` or from a command run inside the branch's
worktree. A finding built on the wrong ref is worse than no finding.
` : `
BINDER UNDER REVIEW: ${BINDER}

GROUND RULE ON SOURCE: a binder's items usually target code that is NOT on the default branch yet.
Before asserting anything about what the code does today, establish which ref actually carries it —
check the binder's \`after\` edges and \`git branch -a\` for a karta/<slug>/integration branch — and read
that ref with \`git show <ref>:<path>\`. A finding built on the wrong ref is worse than no finding.
`

const COMMON = `
Repo: ${ROOT}. karta plans a "binder" of work items and delivers them in waves; each item is built in
an isolated git worktree and gated against its own acceptance oracle. A binder is IMMUTABLE once
delivery starts, so a defect that survives to commit costs a whole delivery.
${TARGET}
HOUSE RULES: minimum table separator (|-|-|), never box-drawing characters. Banned words: "load
bearing", "fencing", "earned its keep"; no arms or destruction metaphors ("landmine", "footgun",
"blast radius") — use "pitfall", "gotcha", "sharp edge", "scope of impact".

You are read-only. Write nothing, commit nothing.
`

const FINDINGS_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['findings', 'sound'],
  properties: {
    sound: { type: 'array', items: { type: 'string' }, description: 'item ids you checked and found sound' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['item_id', 'severity', 'claim', 'evidence', 'narrowest_fix'],
        properties: {
          item_id: { type: 'string', description: 'work item id; or a file path in delivery mode; or "binder"/"delivery" for a whole-target finding' },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
          claim: { type: 'string', description: 'one sentence: what is wrong' },
          evidence: { type: 'string', description: 'file:line or command output — not reasoning' },
          narrowest_fix: { type: 'string' },
        },
      },
    },
  },
}

const BINDER_LENSES = [
  {
    key: 'vacuity',
    prompt: `THE VACUITY LENS — the highest-value lens in this repo, because this is how karta's worst delivery
failed. For every item ask: could an implementer make this oracle green WITHOUT doing what the contract
promises?

Hunt specifically for:
- An assertion that is ALREADY TRUE of the code before its item runs. Check each one against the
  current source. This has been the single most common real defect found in this binder's reviews.
- An assertion that pins an artifact's existence rather than its behaviour.
- A negative control that fails on the wrong input — any input rather than the specific claim.
- A numeric promise asserted against a token name rather than a resolvable value, where the token could
  be a calc(), clamp(), rem or viewport unit and the check would read an expression, not a number.
- A whole-view gate whose exit condition is looser than the assertions suggest.

For each item, actually open the code the item targets and check. An assertion you did not test against
the source is not a finding.`,
  },
  {
    key: 'feasibility',
    prompt: `THE FEASIBILITY LENS — would this plan actually run, as written, against this repo today?

- Run every oracle command and the env_contract command. Report each one's real exit and output.
  Watch for shell quoting defects: a backtick or a $( inside a double-quoted string in a JSON-embedded
  sh -c is command substitution, and silently degrades the pattern being matched.
- Open every path in every item's \`touches\`. Does it exist, or is the item creating it? Is any of them
  a GENERATED mirror under .agents/ or plugins/ that must never be hand-edited?
- Check every file:line citation inside an assertion against the correct ref.
- Does any item depend on a file another binder creates? If so, is that ordering in \`after\`?
- Does the env_contract command actually start what the items need? An item with a visual oracle needs
  a served app; a command that runs tests and exits gives it nothing to poll.`,
  },
  {
    key: 'contradiction',
    prompt: `THE CONTRADICTION LENS — does the binder disagree with itself?

- Two items promising incompatible things about the same element or file.
- A contract clause no assertion checks, or an assertion no contract clause explains.
- Two assertions within one item that cannot both be satisfied.
- A \`depends_on\` order that does not match what the work actually needs — or a serial chain asserting
  an order the work does not require.
- Wording drift: the same concept called two things across items, or a shared_terms entry whose
  canonical string does not appear where it is claimed.
- Counts stated in prose that disagree with what the items actually do.

Pay special attention to text that looks recently edited — patched assertions are where contradictions
enter. If a previous review round's fix created a conflict, say so plainly; that is important signal
about whether patching is still converging.`,
  },
  {
    key: 'scope',
    prompt: `THE SCOPE LENS — is this binder doing its job, and only its job?

Read .karta/sme/karta-house-minimalism.md and apply its Review checklist.

- Any item, contract clause, or assertion serving neither the binder's stated goal nor a named
  constraint.
- Any item that could merge into another with no loss, or be dropped.
- Any place a general mechanism is built where a specific one would do.
- Conversely — and weigh this equally — anything ESSENTIAL that is missing. Under-planning is a defect.
  Does the binder actually close the gap it claims to close?

State the binder's goal in one sentence from its own summary, then judge every item against it.`,
  },
  {
    key: 'consequence',
    prompt: `THE CONSEQUENCE LENS — what does this do to everyone downstream?

karta ships to consumer repos as a plugin, so a change here can fire in every project that installs it.

- Does anything here break a binder that already exists? Check every file in .karta/binders/.
- Does a new rule create a perverse incentive — a cheap way to comply that makes the rule useless?
- Does an escape or waiver land somewhere a human will actually see it?
- Does a gate fire at the right moment, and does the binder say so deliberately?
- Do prose doctrine and enforcement agree, or will a skill still tell a drafter the opposite?
- Does anything need the generated Codex mirrors regenerated, and does the binder say so?`,
  },
  {
    key: 'honesty',
    prompt: `THE HONESTY LENS — does the binder claim more verification than it performs?

This binder's predecessor passed every gate and still shipped four major defects, because each item
honestly disclosed it had no browser and nothing acted on the disclosure.

- Read every \`verification_honesty\` clause. Does the item's oracle actually do what the clause implies,
  and does the clause admit what it cannot settle?
- Where an item defers verification to a later item, does that later item actually cover it? Trace the
  chain and say whether the closing gate can really see what the deferring items got wrong.
- Is any check backstopped by a claim that a later visual comparison "would catch it"? Test that: a
  screenshot at rest cannot see a hover state, a sticky offset, a scroll anchor, or a wrapping behaviour
  that only appears at another width.
- Does any item claim a browser, a network call, or an execution its oracle type does not provide?`,
  },
]

const DELIVERY_LENSES = [
  {
    key: 'promise',
    prompt: `THE PROMISE LENS — every item in this binder passed its acceptance gate. Did the code actually do
what the item's contract promised, or did it only satisfy the letter of the assertions?

This is the highest-value lens here, because a green gate is exactly what stops anyone looking again.

For each item, read its contract and oracle from the binder, then read the diff hunks that item produced
(\`git log --oneline ${RANGE}\` names them; each item commit is subject-tagged \`[karta:item-<id>]\`) and ask:
- Was an assertion already true before the item ran? Check the parent commit.
- Did the item create an artifact — a file, a constant, a check — that nothing actually reads or calls?
  Grep for every new symbol's call sites in the shipped code, not in the self-tests.
- Does a self-test assert the value it also defines, so it can never fail?
- Where an item promised a number is DERIVED rather than typed, is it? Find the literal and prove it is
  computed from the source constant, not restated.
- Is any check written so it would pass on an empty or missing input?

An item you did not open the diff for is not a result. Say which items you actually read.`,
  },
  {
    key: 'runs',
    prompt: `THE RUNS LENS — does the delivered code actually work when executed, not merely when checked?

Work inside the branch's worktree. Find it with \`git worktree list\`; do not check the branch out in the
main checkout and do not modify anything.

- Run the repo's full floor and report each command's real exit code and tail of output:
    uv run scripts/validate_plugin.py --self-test
    uv run scripts/check_shared_copies.py --self-test
    uv run scripts/sync_codex_agents.py --check
    uv run scripts/sync_codex_skills.py --check
- Run whatever self-test suite the changed code carries, and report the real pass/fail counts.
- If the delivery changed a program a person runs, RUN IT. Launch it, drive it to the state the change
  is about, and report what you actually observed — not what the code suggests should happen.
- Report anything that only passes because of the order commands run in, or because of state left in the
  worktree that a fresh clone would not have.

Report real command output. A claim about an exit code you did not observe is a defect in your report.`,
  },
  {
    key: 'contradiction',
    prompt: `THE CONTRADICTION LENS — does this branch disagree with itself, or with what ships beside it?

- Two commits in this range that undo or contradict each other, where the later one is not obviously
  the intended winner.
- Code and its own comment disagreeing.
- Prose the delivery ships — docs, AGENTS.md, a conventions file, a backlog entry — stating something the
  code on this same branch does not do. Check every doc the diff touches against the code it describes.
- A constant defined twice with different values, or a value stated in prose that the code contradicts.
- Wording drift: the same concept named two ways across the delivery's commits and docs.
- Counts stated anywhere in the diff that disagree with what is actually there. Count them yourself.`,
  },
  {
    key: 'scope',
    prompt: `THE SCOPE LENS — did this delivery deliver its binder, and only its binder?

Read .karta/sme/karta-house-minimalism.md and apply its Review checklist to the diff.

- Anything in the diff serving neither an item's contract nor a named constraint. Unrequested work landing
  under cover of a delivery is a real defect, not a bonus.
- Anything the binder promised that is NOT in the diff. Under-delivery is the more expensive half — check
  every item's contract for a clause with no corresponding change.
- A general mechanism built where a specific one would do.
- Dead code, an unused parameter, a config knob nothing reads, a leftover scaffold or debug path.
- Any file left behind that should not ship: scratch files, superpowers content, capture artifacts.

State the binder's goal in one sentence from its own summary, then judge the diff against it.`,
  },
  {
    key: 'consequence',
    prompt: `THE CONSEQUENCE LENS — what does landing this do to everyone downstream?

karta ships to consumer repos as a plugin, so anything under skills/ or agents/ fires in every project
that installs it.

- Are the generated mirrors byte-identical to their canonical sources? Diff them yourself — do not trust
  that a --check passed. Compare skills/<name>/ against .agents/skills/<name>/ and plugins/karta/skills/<name>/.
- Does this change behaviour for an existing consumer repo without saying so? A new gate, a stricter
  check, a changed default, a renamed field.
- Does it break any binder already committed under .karta/binders/ here or invalidate an archived one?
- Does anything need a version bump in .claude-plugin/plugin.json and .codex-plugin/plugin.json, and did
  the delivery do it?
- Does a new check have a cheap way to comply that makes it useless?
- Is any secret, token, absolute local path, or personal detail in the diff?`,
  },
  {
    key: 'honesty',
    prompt: `THE HONESTY LENS — does this branch claim more than it did?

A worker in this delivery once reported a gate verdict it never ran. So test the claims, do not read them.

- Read every commit message in \`git log ${RANGE}\`. Does each one describe what its diff actually does?
  Flag any commit whose message claims a verification, a measurement, or a behaviour the diff does not
  support.
- Read any report, record, or conventions doc the delivery committed. For every number, count, or verdict
  in it, re-derive the number yourself and say whether it holds.
- Where an item deferred verification to a later item, did that later item actually cover it? Trace the
  chain and say plainly what nobody checked.
- Is anything backstopped by "a later visual check would catch it"? Test that claim: a screenshot at rest
  cannot see a hover state, a sticky offset, a scroll anchor, or a wrapping behaviour at another width.
- Does an empty or near-empty commit claim work that is not there?`,
  },
]

const LENSES = MODE === 'delivery' ? DELIVERY_LENSES : BINDER_LENSES

// ---------------------------------------------------------------- Read

phase('Read')

const GROUND_BRIEF = MODE === 'delivery' ? `
Report, with exact citations:
1. The branch's worktree path from \`git worktree list\`, and confirm \`${BRANCH}\` is checked out there and
   its working tree is clean. Every later lens will run commands there, so get this exactly right.
2. \`git log --oneline ${RANGE}\` in full, and \`git diff --stat ${RANGE}\`. Say which commits are item
   builds, which are orchestrator merges, and which are companion phases (gardner, kaizen, archive).
3. Where the binder lives on the branch and its stated goal, in one sentence quoted from its own summary.
   Then the item list: id, oracle type, and whether the item was ACCEPTED with a waiver — read
   \`git log ${RANGE} --format='%H %s%n%b' | grep -i 'Karta-Accept'\` and report every waiver and its reason.
4. The output of every one of these, run in the branch worktree, verbatim with exit codes:
     uv run scripts/validate_plugin.py --self-test
     uv run scripts/check_shared_copies.py --self-test
     uv run scripts/sync_codex_agents.py --check
     uv run scripts/sync_codex_skills.py --check
5. Whether \`${BRANCH}\` merges cleanly into \`${BASE}\`: run \`git merge-tree\` (or a dry merge in a scratch
   worktree — never in the main checkout) and report any conflicting path. Name every file changed on
   BOTH sides since the merge base, since those are where a clean text merge can still be wrong.
6. Any review history: read .karta/roundtable/ for a record naming this branch or slug, so the lenses do
   not re-raise settled ground.
` : `
Report, with exact citations:
1. Which git ref carries the code this binder targets. Check the binder's \`after\` edges, run
   \`git branch -a\`, and say plainly which ref each lens must read and the exact \`git show\` invocation.
2. The binder's stated goal, in one sentence quoted from its own summary.
3. The item list in dependency order: id, estimate, oracle type, design_reference, and whether the item
   is flagged for human review.
4. The output of every one of these, verbatim:
     uv run skills/karta-plan/scripts/validate_binder.py --binder ${BINDER}
     uv run skills/karta-plan/scripts/check_shared_terms.py --binder ${BINDER}
     uv run scripts/validate_plugin.py --self-test
5. What the binder's env_contract command actually does when run — does it serve, or run tests and exit?
6. Any review history you can find: read .karta/roundtable/${SLUG}.json if it exists and report what was
   already reviewed and when, so the lenses do not re-raise settled ground.
`

const ground = await agent(`${COMMON}

YOUR JOB: establish the ground truth every later lens is held to. You are the only agent that runs
before the others, so an error here propagates.
${GROUND_BRIEF}
Under 4000 characters. Facts and citations only.`, { label: 'read:ground-truth', phase: 'Read' })

if (!ground) return { error: 'ground-truth pass died — aborting rather than reviewing on guesses' }

const GROUND = `\n===== GROUND TRUTH (established by a prior read pass — trust these refs) =====\n${ground}\n`

// ---------------------------------------------------------------- Review

phase('Review')

async function inWaves(items, makeThunk, size) {
  const out = []
  for (let i = 0; i < items.length; i += size) {
    out.push(...await parallel(items.slice(i, i + size).map(makeThunk)))
  }
  return out
}

const reviews = await inWaves(LENSES, l => () =>
  agent(`${COMMON}${GROUND}\n${l.prompt}\n${EXTRA ? `\nADDITIONAL FOCUS FROM THE CALLER: ${EXTRA}\n` : ''}
Report only what you can prove by quoting the binder or the source. Reporting zero findings is a
respectable, useful answer — a finding invented to fill space costs more than it is worth. Also list
the item ids you checked and found sound, so coverage is visible.`,
    { label: `review:${l.key}`, phase: 'Review', schema: FINDINGS_SCHEMA })
    .then(r => (r && r.findings ? { lens: l.key, ...r } : null)), 3)

const liveReviews = reviews.filter(r => r && Array.isArray(r.findings))
const raw = liveReviews.flatMap(r => r.findings.map(f => ({ ...f, lens: r.lens })))
log(`review: ${liveReviews.length}/${LENSES.length} lenses returned, ${raw.length} raw findings`)

if (raw.length === 0) {
  return {
    mode: MODE,
    target: MODE === 'delivery' ? `${BRANCH} -> ${BASE} (${RANGE})` : BINDER,
    verdict: 'no findings',
    lenses_returned: liveReviews.map(r => r.lens),
    sound: liveReviews.flatMap(r => r.sound || []),
    ground_truth: ground,
  }
}

// ---------------------------------------------------------------- Verify

phase('Verify')

const VERDICT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['disposition', 'evidence', 'severity_actual', 'structural', 'narrowest_fix'],
  properties: {
    disposition: { type: 'string', enum: ['CONFIRMED', 'REFUTED', 'PARTIAL'] },
    evidence: { type: 'string', description: 'what you found when you checked — file:line or command output' },
    severity_actual: { type: 'string', enum: ['blocking', 'major', 'minor', 'none'] },
    structural: { type: 'boolean', description: 'true if the fix changes an item type, the item set, or an item purpose — not just wording' },
    narrowest_fix: { type: 'string' },
  },
}

const verified = await inWaves(raw, (f, i) => () =>
  agent(`${COMMON}${GROUND}

A review lens raised this finding. Your job is to REFUTE it if you can — assume it is wrong until the
source says otherwise. A refuted finding is the most valuable result you can return, because it stops a
binder being edited for no reason.

ITEM: ${f.item_id}
LENS: ${f.lens}
CLAIMED SEVERITY: ${f.severity}
CLAIM: ${f.claim}
EVIDENCE OFFERED: ${f.evidence}
PROPOSED FIX: ${f.narrowest_fix}

Open the cited file at the cited ref and read it. Run the cited command. Check the binder text verbatim.
Then judge:
- CONFIRMED: the defect is real exactly as stated.
- PARTIAL: something real is there, but not what was claimed — say what is actually true.
- REFUTED: the premise does not hold. Quote what you found instead.

Set severity_actual by real impact, not by what the lens claimed. Set structural=true only if the honest
fix changes an item's oracle type, the item set, or what an item is for.`,
    { label: `verify:${f.item_id}:${f.lens}`, phase: 'Verify', schema: VERDICT_SCHEMA })
    .then(v => (v ? { ...f, ...v } : null)), 4)

const judged = verified.filter(Boolean)
const confirmed = judged.filter(v => v.disposition !== 'REFUTED')
const blocking = confirmed.filter(v => v.severity_actual === 'blocking')
const structural = confirmed.filter(v => v.structural)
log(`verify: ${judged.length} judged, ${confirmed.length} stand, ${blocking.length} blocking, ${structural.length} structural`)

// ---------------------------------------------------------------- Report

phase('Report')

const TABLE = judged.map(v =>
  `\n--- ${v.item_id} / ${v.lens} [${v.disposition}] severity=${v.severity_actual} structural=${v.structural}\n` +
  `CLAIM: ${v.claim}\nVERIFIED: ${v.evidence}\nFIX: ${v.narrowest_fix}\n`).join('')

const REPORT_VERDICTS = MODE === 'delivery'
  ? '"land it", "fix then land", "hold the branch", or "revert named items"'
  : '"commit it", "patch then commit", "rethink named items", or "replan"'

const report = await agent(`${COMMON}${GROUND}

Six adversarial lenses reviewed this ${MODE === 'delivery' ? 'delivered branch' : 'binder'} and every
finding was then independently verified against the source. Here is everything, with verdicts:
${TABLE}

YOUR JOB: write the review a human will read and act on. This is advisory — it is NOT a multi-model
roundtable review and must never be recorded as one — so its value is entirely in being honest and
sharply prioritized.

Structure it as:

1. VERDICT — one of ${REPORT_VERDICTS}. One sentence of justification. Be willing to say it is fine;
   three clean lenses is a real result.
2. BLOCKING — findings that would let this ${MODE === 'delivery' ? 'branch land while its binder\'s stated goal is unmet, or that break something on the default branch' : 'binder pass its gates while its stated goal is unmet, or that make an item unbuildable'}.
   For each: item or file, what is wrong, the evidence, the exact edit.
3. WORTH FIXING — real but not blocking. Same format, kept short.
4. SURFACED, NOT FOR THIS ${MODE === 'delivery' ? 'DELIVERY' : 'BINDER'} — real problems belonging to karta
   itself or to other work. Say where each belongs, and name the backlog entry it should become. Do not
   let these hold up the target.
5. REFUTED — every finding the verify pass killed, one line each with the evidence that killed it. This
   section matters: it is what stops the next review round re-raising settled ground.
6. COVERAGE — which items each lens actually examined, and anything no lens looked at.

Apply ruthless prioritization: a finding that does not address the binder's stated goal directly goes in
section 4 or 5, never section 2. If several findings cluster in one area, say so — that is a signal the
area needs rethinking rather than patching.

Write it in plain language a person can act on without rereading. No preamble.`,
  { label: 'report:advisory', phase: 'Report' })

return {
  mode: MODE,
  target: MODE === 'delivery' ? `${BRANCH} -> ${BASE} (${RANGE})` : BINDER,
  advisory_review: report,
  counts: {
    raw: raw.length, judged: judged.length, confirmed: confirmed.length,
    blocking: blocking.length, structural: structural.length,
    refuted: judged.length - confirmed.length,
  },
  blocking: blocking.map(v => ({ item: v.item_id, claim: v.claim, fix: v.narrowest_fix })),
  recordable_as_multimodel: false,
}
