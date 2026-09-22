import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { access, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import test from "node:test";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import type { KartaBuildItemRunner } from "../../extensions/pi/build-runner.ts";
import { KartaDeliveryRunner, MAX_PARALLEL_BUILDS, mapWithConcurrencyLimit } from "../../extensions/pi/delivery-runner.ts";
import { DispatchLockManager } from "../../extensions/pi/dispatch-lock.ts";
import type { KartaIntegrationRunner } from "../../extensions/pi/integration-runner.ts";
import { LifecycleRegistry } from "../../extensions/pi/lifecycle-registry.ts";
import { KartaProcessManager } from "../../extensions/pi/process-manager.ts";
import type { KartaWaveRunner } from "../../extensions/pi/wave-runner.ts";
import type { KartaBuildWorkerRunner } from "../../extensions/pi/worker-runner.ts";

const exec = promisify(execFile);

async function git(cwd: string, args: string[]): Promise<string> {
  const { stdout } = await exec("git", ["-C", cwd, ...args], { encoding: "utf8" });
  return stdout.trim();
}

async function fixture(
  collide = false,
  seedIntegration = true,
): Promise<{ repo: string; root: string; cleanup(): Promise<void> }> {
  const root = await mkdtemp(join(tmpdir(), "karta-delivery-runner-"));
  const repo = join(root, "repo");
  await mkdir(join(repo, ".karta", "binders"), { recursive: true });
  await writeFile(
    join(repo, ".karta", "binders", "demo.json"),
    `${JSON.stringify({
      slug: "demo",
      title: "Delivery fixture",
      summary: "Deliver graph",
      motivation: "Prove waves",
      scope: { included: ["a.txt", "b.txt", "c.txt"] },
      work_items: [
        {
          id: "item-a",
          title: "A",
          summary: "A",
          touches: ["shared.txt"],
          depends_on: [],
          oracle: { type: "unit", assertions: ["A"] , opt_out: true, reason: "fixture" },
        },
        {
          id: "item-b",
          title: "B",
          summary: "B",
          touches: [collide ? "shared.txt" : "other.txt"],
          depends_on: [],
          oracle: { type: "unit", assertions: ["B"], opt_out: true, reason: "fixture" },
        },
        {
          id: "item-c",
          title: "C",
          summary: "C",
          touches: ["last.txt"],
          depends_on: ["item-a", "item-b"],
          oracle: { type: "unit", assertions: ["C"], opt_out: true, reason: "fixture" },
        },
      ],
    })}\n`,
  );
  await writeFile(join(repo, "base.txt"), "base\n");
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Delivery"]);
  await git(repo, ["config", "user.email", "delivery@example.invalid"]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "base"]);
  if (seedIntegration) await git(repo, ["branch", "karta/demo/integration"]);
  return {
    repo,
    root,
    cleanup: () => rm(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }),
  };
}

function createRunner(
  repo: string,
  beforeBuild: () => Promise<void> = async () => {},
): { runner: KartaDeliveryRunner; maxParallel(): number } {
  const locks = new DispatchLockManager();
  const processes = new KartaProcessManager(new LifecycleRegistry(), 10);
  let active = 0;
  let maximum = 0;
  const builds = {
    async runWithLease(_ctx: unknown, binder: string, item: string) {
      active += 1;
      await beforeBuild();
      maximum = Math.max(maximum, active);
      await new Promise((resolve) => setTimeout(resolve, 20));
      const integration = await git(repo, ["rev-parse", `refs/heads/karta/${binder}/integration`]);
      const tree = await git(repo, ["rev-parse", `${integration}^{tree}`]);
      const commit = await git(repo, [
        "commit-tree",
        tree,
        "-p",
        integration,
        "-m",
        `[karta:item-${item}] fixture`,
      ]);
      await git(repo, ["update-ref", `refs/heads/karta/${binder}/item-${item}`, commit]);
      await git(repo, ["update-ref", `refs/karta/${binder}/item-${item}/built`, commit]);
      active -= 1;
      return {
        schema: "karta-build-item-v1",
        binder,
        item,
        status: "built",
        recoveryState: "not-started",
        attempts: 1,
        commit,
        message: "built",
        worker: { checks: [{ id: "floor", command: "true", cwd: "." }] },
      };
    },
  } as unknown as KartaBuildItemRunner;
  const integrations = {
    async integrate(
      _ctx: unknown,
      binder: string,
      item: string,
      _worktree: string,
      _lease: unknown,
      _checks: unknown,
      _process: unknown,
      acceptance?: { authorize(findings: unknown[]): Promise<{ reason: string } | undefined> },
    ) {
      const base = await git(repo, ["rev-parse", `refs/heads/karta/${binder}/integration`]);
      const itemTip = await git(repo, ["rev-parse", `refs/heads/karta/${binder}/item-${item}`]);
      const tree = await git(repo, ["rev-parse", `${base}^{tree}`]);
      const authorization = acceptance
        ? await acceptance.authorize([{
            code: "fixture-gap",
            message: "Fixture acceptance gap.",
            severity: "major",
          }])
        : undefined;
      if (acceptance && !authorization) {
        return {
          schema: "karta-integration-item-v1",
          binder,
          item,
          status: "blocked",
          base,
          itemTip,
          message: "cancelled",
        };
      }
      const message = acceptance
        ? `[karta:merge-item-${item}] fixture\n\nKarta-Accepted: fixture-gap\nKarta-Accept-Reason: ${authorization!.reason}`
        : `[karta:merge-item-${item}] fixture`;
      const merge = await git(repo, [
        "commit-tree",
        tree,
        "-p",
        base,
        "-p",
        itemTip,
        "-m",
        message,
      ]);
      await git(repo, ["update-ref", `refs/heads/karta/${binder}/integration`, merge, base]);
      await git(repo, ["update-ref", `refs/karta/${binder}/item-${item}/done`, merge]);
      if (acceptance) {
        await git(repo, ["update-ref", "-d", `refs/karta/${binder}/item-${item}/failed`, itemTip]);
        await git(repo, ["update-ref", `refs/karta/${binder}/item-${item}/accepted`, itemTip]);
      }
      return {
        schema: "karta-integration-item-v1",
        binder,
        item,
        status: "integrated",
        base,
        itemTip,
        mergeCommit: merge,
        accepted: Boolean(acceptance),
        message: "integrated",
      };
    },
  } as unknown as KartaIntegrationRunner;
  const workers = {
    async run() {
      return {
        outcome: "ready",
        checks: [{ id: "floor", command: "true", cwd: "." }],
      };
    },
  } as unknown as KartaBuildWorkerRunner;
  const waves = {
    async start(binder: string, wave: number) {
      return {
        binder,
        wave,
        base: await git(repo, ["rev-parse", `refs/heads/karta/${binder}/integration`]),
        baseTag: `refs/tags/karta/${binder}/wave-${wave}-base`,
      };
    },
    async finish(_ctx: unknown, anchor: unknown) {
      return {
        schema: "karta-wave-finalization-v1",
        status: "passed",
        anchor,
        tip: await git(repo, ["rev-parse", "refs/heads/karta/demo/integration"]),
        successTag: "refs/tags/karta/demo/wave-fixture",
        message: "passed",
      };
    },
  } as unknown as KartaWaveRunner;
  const companions = {
    async finishDelivery() {
      const tip = await git(repo, ["rev-parse", "refs/heads/karta/demo/integration"]);
      return {
        schema: "karta-companions-v1" as const,
        docGardner: { role: "doc-gardner" as const, status: "disabled" as const },
        kaizen: { role: "kaizen" as const, status: "disabled" as const },
        archive: { status: "committed" as const, commit: tip },
      };
    },
  };
  return {
    runner: new KartaDeliveryRunner(locks, processes, builds, integrations, workers, waves, companions),
    maxParallel: () => maximum,
  };
}

test("a first run creates the integration branch from HEAD when none exists", async () => {
  const state = await fixture(false, false);
  try {
    await assert.rejects(() =>
      git(state.repo, ["rev-parse", "--verify", "refs/heads/karta/demo/integration"]),
    );
    const base = await git(state.repo, ["rev-parse", "HEAD"]);
    const delivery = createRunner(state.repo);
    const result = await delivery.runner.run({ cwd: state.repo } as ExtensionContext, "demo");
    assert.equal(result.status, "complete");
    const created = await git(state.repo, ["rev-parse", "refs/heads/karta/demo/integration"]);
    assert.ok(created);
    await assert.doesNotReject(() =>
      git(state.repo, ["merge-base", "--is-ancestor", base, created]),
    );
  } finally {
    await state.cleanup();
  }
});

test("delivery builds dependency-ready items in parallel and integrates them FIFO", async () => {
  const state = await fixture();
  try {
    const delivery = createRunner(state.repo);
    const result = await delivery.runner.run({ cwd: state.repo } as ExtensionContext, "demo");
    assert.equal(result.status, "complete");
    assert.deepEqual(result.waves.map((wave) => wave.items), [
      ["item-a", "item-b"],
      ["item-c"],
    ]);
    assert.equal(delivery.maxParallel(), 2);
    for (const item of ["item-a", "item-b", "item-c"]) {
      assert.ok(await git(state.repo, ["rev-parse", `refs/karta/demo/item-${item}/done`]));
    }
  } finally {
    await state.cleanup();
  }
});

async function seedFailedItem(repo: string, item = "item-a"): Promise<string> {
  const integration = await git(repo, ["rev-parse", "refs/heads/karta/demo/integration"]);
  const tree = await git(repo, ["rev-parse", `${integration}^{tree}`]);
  const commit = await git(repo, [
    "commit-tree",
    tree,
    "-p",
    integration,
    "-m",
    `[karta:item-${item}] failed fixture`,
  ]);
  await git(repo, ["update-ref", `refs/heads/karta/demo/item-${item}`, commit]);
  await git(repo, ["update-ref", `refs/karta/demo/item-${item}/failed`, commit]);
  return commit;
}

test("a second delivery process cannot enter the same binder lease", async () => {
  const state = await fixture();
  try {
    let releaseBuild!: () => void;
    let reportEntered!: () => void;
    const entered = new Promise<void>((resolve) => { reportEntered = resolve; });
    const blocked = new Promise<void>((resolve) => { releaseBuild = resolve; });
    let reported = false;
    const first = createRunner(state.repo, async () => {
      if (!reported) {
        reported = true;
        reportEntered();
      }
      await blocked;
    });
    const firstRun = first.runner.run({ cwd: state.repo } as ExtensionContext, "demo");
    await entered;
    const second = createRunner(state.repo);
    await assert.rejects(
      () => second.runner.run({ cwd: state.repo } as ExtensionContext, "demo"),
      /already locked|lock/i,
    );
    releaseBuild();
    assert.equal((await firstRun).status, "complete");
  } finally {
    await state.cleanup();
  }
});

test("interactive human acceptance records reason and resumes delivery", async () => {
  const state = await fixture();
  try {
    const itemTip = await seedFailedItem(state.repo);
    const delivery = createRunner(state.repo);
    const prompts: string[] = [];
    const ctx = {
      cwd: state.repo,
      hasUI: true,
      ui: {
        async select(message: string) {
          prompts.push(message);
          if (message.includes("carries state from an earlier run")) return "Resume";
          return "Accept exact current findings";
        },
        async confirm(_title: string, message: string) {
          prompts.push(message);
          return true;
        },
        async input() {
          prompts.push("input");
          return "Approved fixture gap.";
        },
      },
    } as unknown as ExtensionContext;
    const result = await delivery.runner.run(ctx, "demo");
    assert.equal(result.status, "complete");
    assert.ok(prompts.some((prompt) => prompt.includes("fixture-gap")));
    assert.equal(
      await git(state.repo, ["rev-parse", "refs/karta/demo/item-item-a/accepted"]),
      itemTip,
    );
    await assert.rejects(() =>
      git(state.repo, ["rev-parse", "--verify", "refs/karta/demo/item-item-a/failed"]),
    );
  } finally {
    await state.cleanup();
  }
});

test("fix-and-rerun clears only the expected failed ref and rebuilds", async () => {
  const state = await fixture();
  try {
    const failedTip = await seedFailedItem(state.repo);
    const delivery = createRunner(state.repo);
    const ctx = {
      cwd: state.repo,
      hasUI: true,
      ui: {
        async select(message: string) {
          return message.includes("carries state from an earlier run") ? "Resume" : "Fix and rerun";
        },
      },
    } as unknown as ExtensionContext;
    const result = await delivery.runner.run(ctx, "demo");
    assert.equal(result.status, "complete");
    const finalItemTip = await git(state.repo, ["rev-parse", "refs/heads/karta/demo/item-item-a"]);
    assert.notEqual(finalItemTip, failedTip);
    await assert.rejects(() =>
      git(state.repo, ["rev-parse", "--verify", "refs/karta/demo/item-item-a/failed"]),
    );
  } finally {
    await state.cleanup();
  }
});

test("defer leaves the failed ref intact and stops without model authority", async () => {
  const state = await fixture();
  try {
    const itemTip = await seedFailedItem(state.repo);
    const delivery = createRunner(state.repo);
    const ctx = {
      cwd: state.repo,
      hasUI: true,
      ui: {
        async select(message: string) {
          return message.includes("carries state from an earlier run")
            ? "Resume"
            : "Defer and stop delivery";
        },
      },
    } as unknown as ExtensionContext;
    const result = await delivery.runner.run(ctx, "demo");
    assert.equal(result.status, "blocked");
    assert.match(result.message, /remains deferred/);
    assert.equal(
      await git(state.repo, ["rev-parse", "refs/karta/demo/item-item-a/failed"]),
      itemTip,
    );
  } finally {
    await state.cleanup();
  }
});

test("leftover state is never resumed or cleared silently without a host prompt", async () => {
  const state = await fixture();
  try {
    await seedFailedItem(state.repo);
    const leftovers = (await git(state.repo, ["for-each-ref", "--format=%(refname)", "refs/karta/demo/"])).trim();
    assert.notEqual(leftovers, "");
    const delivery = createRunner(state.repo);
    const result = await delivery.runner.run({ cwd: state.repo } as ExtensionContext, "demo");
    assert.equal(result.status, "blocked");
    assert.match(result.message, /rerun interactively to choose resume or clear/);
    // Nothing was resumed and nothing was cleared: the state is exactly as it was.
    assert.equal(
      (await git(state.repo, ["for-each-ref", "--format=%(refname)", "refs/karta/demo/"])).trim(),
      leftovers,
    );
  } finally {
    await state.cleanup();
  }
});

test("choosing Clear removes the earlier run's state and starts over", async () => {
  const state = await fixture();
  try {
    await seedFailedItem(state.repo);
    assert.notEqual(
      (await git(state.repo, ["for-each-ref", "--format=%(refname)", "refs/karta/demo/"])).trim(),
      "",
    );
    const delivery = createRunner(state.repo);
    const ctx = {
      cwd: state.repo,
      hasUI: true,
      ui: {
        async select(message: string) {
          return message.includes("carries state from an earlier run") ? "Clear" : "Fix and rerun";
        },
      },
    } as unknown as ExtensionContext;
    const result = await delivery.runner.run(ctx, "demo");
    assert.equal(result.status, "complete");
    // The seeded failed ref is gone, because the run started from wave 1.
    await assert.rejects(() =>
      git(state.repo, ["rev-parse", "--verify", "refs/karta/demo/item-item-a/failed"]),
    );
  } finally {
    await state.cleanup();
  }
});

test("a wave's builds are capped so a wide binder cannot start every item at once", async () => {
  let inFlight = 0;
  let peak = 0;
  const items = Array.from({ length: 12 }, (_, index) => index);
  const results = await mapWithConcurrencyLimit(items, MAX_PARALLEL_BUILDS, async (item) => {
    inFlight += 1;
    peak = Math.max(peak, inFlight);
    await new Promise((settle) => setTimeout(settle, 5));
    inFlight -= 1;
    return item * 2;
  });
  assert.ok(peak <= MAX_PARALLEL_BUILDS, `peak in-flight was ${peak}`);
  assert.ok(peak > 1, "the cap must still allow real parallelism");
  assert.deepEqual(results, items.map((item) => item * 2));
});

test("a dirty worktree is disclosed before Clear, then removed with consent", async () => {
  const state = await fixture();
  try {
    await seedFailedItem(state.repo);
    // A leftover item worktree at the exact path the runner uses, holding uncommitted work.
    const worktreesRoot = join(state.root, "repo-worktrees");
    await mkdir(worktreesRoot, { recursive: true });
    const worktree = join(worktreesRoot, "karta-demo-item-item-a");
    await git(state.repo, ["worktree", "add", worktree, "karta/demo/item-item-a"]);
    await writeFile(join(worktree, "uncommitted.txt"), "work in progress\n");

    const delivery = createRunner(state.repo);
    const messages: string[] = [];
    const ctx = {
      cwd: state.repo,
      hasUI: true,
      ui: {
        async select(message: string) {
          messages.push(message);
          return message.includes("carries state from an earlier run") ? "Clear" : "Fix and rerun";
        },
      },
    } as unknown as ExtensionContext;
    const result = await delivery.runner.run(ctx, "demo");
    assert.equal(result.status, "complete");
    // The human was told which worktree would go and that its work would be lost.
    const disclosure = messages.find((message) => message.includes("carries state from an earlier run"));
    assert.ok(disclosure);
    assert.match(disclosure, /will also delete these worktrees/);
    assert.match(disclosure, /karta-demo-item-item-a/);
    // The worktree was really removed and rebuilt, so the next run cannot wedge on a half-sweep.
    await assert.rejects(() => access(join(worktree, "uncommitted.txt")));
  } finally {
    await state.cleanup();
  }
});

test("a worktree that turns dirty between the prompt and the sweep stops the clear", async () => {
  const state = await fixture();
  try {
    await seedFailedItem(state.repo);
    const worktreesRoot = join(state.root, "repo-worktrees");
    await mkdir(worktreesRoot, { recursive: true });
    const worktree = join(worktreesRoot, "karta-demo-item-item-a");
    await git(state.repo, ["worktree", "add", worktree, "karta/demo/item-item-a"]);

    const delivery = createRunner(state.repo);
    const ctx = {
      cwd: state.repo,
      hasUI: true,
      ui: {
        async select(message: string) {
          if (!message.includes("carries state from an earlier run")) return "Fix and rerun";
          // Clean when the prompt inspected it, dirty by the time the sweep runs — the case the
          // consent flag cannot cover, because consent was given about a clean worktree.
          await writeFile(join(worktree, "snuck-in.txt"), "written during the prompt\n");
          return "Clear";
        },
      },
    } as unknown as ExtensionContext;
    await assert.rejects(() => delivery.runner.run(ctx, "demo"), /holds uncommitted work/);
    // The work survived, which is the whole point of failing here instead of sweeping.
    await access(join(worktree, "snuck-in.txt"));
  } finally {
    await state.cleanup();
  }
});

test("declared collision surfaces serialize otherwise-ready items", async () => {
  const state = await fixture(true);
  try {
    const delivery = createRunner(state.repo);
    const result = await delivery.runner.run({ cwd: state.repo } as ExtensionContext, "demo");
    assert.equal(result.status, "complete");
    assert.deepEqual(result.waves.map((wave) => wave.items), [
      ["item-a"],
      ["item-b"],
      ["item-c"],
    ]);
    assert.equal(delivery.maxParallel(), 1);
  } finally {
    await state.cleanup();
  }
});
