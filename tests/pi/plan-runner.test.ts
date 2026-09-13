import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { KARTA_PLAN_COMMIT_SUBJECT, KartaPlanRunner } from "../../extensions/pi/plan-runner.ts";

const exec = promisify(execFile);
const ROOT = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const EXAMPLE_BINDER = join(ROOT, "skills", "karta-plan", "references", "example-binder.json");
const SLUG = "notifications-redesign";

async function git(cwd: string, args: string[]): Promise<string> {
  return (await exec("git", args, { cwd })).stdout.trim();
}

interface Fixture {
  root: string;
  cleanup: () => Promise<void>;
}

/** A repository holding one valid draft binder and nothing else staged. */
async function fixture(): Promise<Fixture> {
  const root = await mkdtemp(join(tmpdir(), "karta-plan-runner-"));
  await git(root, ["init", "--initial-branch=main"]);
  await git(root, ["config", "user.email", "karta@example.test"]);
  await git(root, ["config", "user.name", "Karta Test"]);
  await git(root, ["commit", "--allow-empty", "-m", "init"]);
  await mkdir(join(root, ".karta", "binders"), { recursive: true });
  await writeFile(join(root, ".karta", "binders", `${SLUG}.json`), await readFile(EXAMPLE_BINDER, "utf8"));
  return {
    root,
    cleanup: async () => {
      await rm(root, { recursive: true, force: true });
    },
  };
}

function interactive(
  root: string,
  answers: { review?: boolean; verb?: string } = {},
): ExtensionContext {
  return {
    cwd: root,
    hasUI: true,
    ui: {
      async confirm() {
        return answers.review ?? true;
      },
      async select() {
        return answers.verb ?? "commit";
      },
    },
  } as unknown as ExtensionContext;
}

function headless(root: string): ExtensionContext {
  return { cwd: root, hasUI: false, ui: {} } as unknown as ExtensionContext;
}

test("plan survey returns package-owned stack and pack facts", async () => {
  const state = await fixture();
  try {
    const result = await new KartaPlanRunner().survey(interactive(state.root));
    assert.equal(result.schema, "karta-plan-survey-v1");
    assert.deepEqual(
      result.runs.map((run) => run.action),
      ["detectStack", "checkPackProvenance"],
    );
    assert.equal(result.runs.every((run) => run.code === 0), true);
    assert.equal(result.runs[0].stdout.trim().startsWith("{"), true);
  } finally {
    await state.cleanup();
  }
});

test("a binder slug that is not a slug is refused before anything runs", async () => {
  const state = await fixture();
  try {
    await assert.rejects(
      () => new KartaPlanRunner().commit(interactive(state.root), "Not A Slug"),
      /Invalid Karta binder slug/,
    );
  } finally {
    await state.cleanup();
  }
});

test("committing a binder that was never drafted is refused", async () => {
  const state = await fixture();
  try {
    await assert.rejects(
      () => new KartaPlanRunner().commit(interactive(state.root), "never-drafted"),
      /no draft binder/,
    );
  } finally {
    await state.cleanup();
  }
});

test("an invalid binder cannot be committed, and the validator's reasons surface", async () => {
  const state = await fixture();
  try {
    const before = await git(state.root, ["rev-parse", "HEAD"]);
    await writeFile(join(state.root, ".karta", "binders", "bad.json"), JSON.stringify({ slug: "bad", title: "x" }));
    await assert.rejects(
      () => new KartaPlanRunner().commit(interactive(state.root), "bad"),
      (error: Error) => {
        assert.match(error.message, /failed validation/);
        assert.match(error.message, /missing required property 'work_items'/);
        return true;
      },
    );
    assert.equal(await git(state.root, ["rev-parse", "HEAD"]), before);
  } finally {
    await state.cleanup();
  }
});

test("a binder is refused when unrelated changes are already staged", async () => {
  const state = await fixture();
  try {
    await writeFile(join(state.root, "unrelated.txt"), "not part of the plan of record\n");
    await git(state.root, ["add", "--", "unrelated.txt"]);
    await assert.rejects(
      () => new KartaPlanRunner().commit(interactive(state.root), SLUG),
      /unrelated changes staged: unrelated.txt/,
    );
  } finally {
    await state.cleanup();
  }
});

test("without a host prompt the commit is blocked rather than assumed", async () => {
  const state = await fixture();
  try {
    const before = await git(state.root, ["rev-parse", "HEAD"]);
    const result = await new KartaPlanRunner().commit(headless(state.root), SLUG);
    assert.equal(result.status, "blocked");
    assert.match(result.message, /needs a human at a prompt/);
    assert.equal(await git(state.root, ["rev-parse", "HEAD"]), before);
  } finally {
    await state.cleanup();
  }
});

test("declining the review cancels the commit and leaves the tree untouched", async () => {
  const state = await fixture();
  try {
    const before = await git(state.root, ["rev-parse", "HEAD"]);
    const binderPath = join(state.root, ".karta", "binders", `${SLUG}.json`);
    const binderBefore = await readFile(binderPath, "utf8");
    const result = await new KartaPlanRunner().commit(interactive(state.root, { review: false }), SLUG);
    assert.equal(result.status, "cancelled");
    assert.equal(await git(state.root, ["rev-parse", "HEAD"]), before);
    // The binder itself must be untouched — asserting an empty index here would be trivially
    // true, since this path never stages anything.
    assert.equal(await readFile(binderPath, "utf8"), binderBefore);
    // Untracked, so the cancelled path staged nothing on its way out.
    await assert.rejects(() => git(state.root, ["ls-files", "--error-unmatch", `.karta/binders/${SLUG}.json`]));
  } finally {
    await state.cleanup();
  }
});

test("reviewing without giving the commit verb cancels the commit", async () => {
  const state = await fixture();
  try {
    const before = await git(state.root, ["rev-parse", "HEAD"]);
    const result = await new KartaPlanRunner().commit(interactive(state.root, { verb: "cancel" }), SLUG);
    assert.equal(result.status, "cancelled");
    assert.match(result.message, /`commit` verb was not given/);
    assert.equal(await git(state.root, ["rev-parse", "HEAD"]), before);
  } finally {
    await state.cleanup();
  }
});

test("on the commit verb the validated binder lands as the whole commit", async () => {
  const state = await fixture();
  try {
    const before = await git(state.root, ["rev-parse", "HEAD"]);
    const result = await new KartaPlanRunner().commit(interactive(state.root), SLUG);
    assert.equal(result.status, "committed");
    assert.ok(result.commit);
    assert.notEqual(result.commit, before);
    assert.equal(await git(state.root, ["rev-parse", "HEAD"]), result.commit);
    assert.equal(await git(state.root, ["log", "-1", "--format=%s"]), KARTA_PLAN_COMMIT_SUBJECT(SLUG));
    assert.equal(
      await git(state.root, ["show", "--name-only", "--format=", "HEAD"]),
      `.karta/binders/${SLUG}.json`,
    );
    // The card carries the recorded escapes the human is meant to see.
    assert.match(result.card, /VALID\./);
    assert.match(result.card, /opt-out:/);
    assert.match(result.card, /Shape: one binder/);
  } finally {
    await state.cleanup();
  }
});

test("a binder edited while the review prompt is open is refused, not committed", async () => {
  const state = await fixture();
  try {
    const before = await git(state.root, ["rev-parse", "HEAD"]);
    const binderPath = join(state.root, ".karta", "binders", `${SLUG}.json`);
    // The window the hash check exists for: the file changes after validation and after the
    // human was shown a card describing the validated bytes.
    const ctx = {
      cwd: state.root,
      hasUI: true,
      ui: {
        async confirm() {
          return true;
        },
        async select() {
          const draft = JSON.parse(await readFile(binderPath, "utf8")) as Record<string, unknown>;
          draft.title = "Rewritten while the human was reading the card";
          await writeFile(binderPath, JSON.stringify(draft, null, 2));
          return "commit";
        },
      },
    } as unknown as ExtensionContext;
    await assert.rejects(
      () => new KartaPlanRunner().commit(ctx, SLUG),
      /changed after it was validated/,
    );
    assert.equal(await git(state.root, ["rev-parse", "HEAD"]), before);
  } finally {
    await state.cleanup();
  }
});

test("a commit cannot sweep in a review record it does not own", async () => {
  const state = await fixture();
  try {
    await mkdir(join(state.root, ".karta", "roundtable"), { recursive: true });
    await writeFile(join(state.root, ".karta", "roundtable", "someone-elses-binder.json"), "{}\n");
    await git(state.root, ["add", "--", ".karta/roundtable/someone-elses-binder.json"]);
    await assert.rejects(
      () => new KartaPlanRunner().commit(interactive(state.root), SLUG),
      /unrelated changes staged: .karta\/roundtable\/someone-elses-binder\.json/,
    );
  } finally {
    await state.cleanup();
  }
});

test("a repository that requires a review record does not get a record-less commit", async () => {
  const state = await fixture();
  try {
    await writeFile(
      join(state.root, ".karta", "roundtable.json"),
      JSON.stringify({ enabled: true, ledger: true, points: { plan_commit: true, deliver_merge: true } }),
    );
    await assert.rejects(
      () => new KartaPlanRunner().commit(interactive(state.root), SLUG),
      /without its review record staged/,
    );
    // With the record and its ledger staged, the same commit proceeds and carries all three.
    await mkdir(join(state.root, ".karta", "roundtable"), { recursive: true });
    await writeFile(join(state.root, ".karta", "roundtable", `${SLUG}.json`), "{}\n");
    await writeFile(join(state.root, ".karta", "roundtable", `${SLUG}.rounds.json`), "{}\n");
    await git(state.root, [
      "add",
      "--",
      `.karta/roundtable/${SLUG}.json`,
      `.karta/roundtable/${SLUG}.rounds.json`,
    ]);
    const result = await new KartaPlanRunner().commit(interactive(state.root), SLUG);
    assert.equal(result.status, "committed");
    assert.deepEqual(
      (await git(state.root, ["show", "--name-only", "--format=", "HEAD"])).split("\n").sort(),
      [
        `.karta/binders/${SLUG}.json`,
        `.karta/roundtable/${SLUG}.json`,
        `.karta/roundtable/${SLUG}.rounds.json`,
      ].sort(),
    );
    assert.match(result.card, /Review record staged: /);
  } finally {
    await state.cleanup();
  }
});

test("KARTA_SKIP_ROUNDTABLE is the hatch when the review environment is down", async () => {
  const state = await fixture();
  const previous = process.env.KARTA_SKIP_ROUNDTABLE;
  try {
    await writeFile(
      join(state.root, ".karta", "roundtable.json"),
      JSON.stringify({ enabled: true, ledger: true, points: { plan_commit: true } }),
    );
    process.env.KARTA_SKIP_ROUNDTABLE = "1";
    const result = await new KartaPlanRunner().commit(interactive(state.root), SLUG);
    assert.equal(result.status, "committed");
  } finally {
    if (previous === undefined) delete process.env.KARTA_SKIP_ROUNDTABLE;
    else process.env.KARTA_SKIP_ROUNDTABLE = previous;
    await state.cleanup();
  }
});

test("a repeated slug in a set is refused", async () => {
  const state = await fixture();
  try {
    await assert.rejects(
      () => new KartaPlanRunner().commit(interactive(state.root), SLUG, [SLUG]),
      /cannot repeat a slug/,
    );
  } finally {
    await state.cleanup();
  }
});

test("a binder set commits together, as one commit", async () => {
  const state = await fixture();
  try {
    const second = "notifications-redesign-edit";
    const draft = JSON.parse(
      await readFile(join(state.root, ".karta", "binders", `${SLUG}.json`), "utf8"),
    ) as Record<string, unknown>;
    draft.slug = second;
    draft.title = "Tag editing — rewire call sites";
    draft.after = [SLUG];
    await writeFile(
      join(state.root, ".karta", "binders", `${second}.json`),
      JSON.stringify(draft, null, 2),
    );

    const result = await new KartaPlanRunner().commit(interactive(state.root), SLUG, [second]);
    assert.equal(result.status, "committed");
    assert.deepEqual(result.binders, [SLUG, second]);
    assert.equal(await git(state.root, ["rev-parse", "HEAD"]), result.commit);
    assert.deepEqual(
      (await git(state.root, ["show", "--name-only", "--format=", "HEAD"])).split("\n").sort(),
      [`.karta/binders/${SLUG}.json`, `.karta/binders/${second}.json`].sort(),
    );
    assert.match(result.card, /Binder set — 2 binders, committed together/);
    assert.match(result.card, new RegExp(`after ${SLUG}`));
  } finally {
    await state.cleanup();
  }
});
