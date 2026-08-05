import { execFile } from "node:child_process";
import { promisify } from "node:util";

const exec = promisify(execFile);
const IDENTIFIER = /^[a-z0-9][a-z0-9-]*$/;

export type KartaItemRecoveryState =
  | "not-started"
  | "branch-only"
  | "worktree-dirty"
  | "committed-unmarked"
  | "built"
  | "failed"
  | "merged-unmarked"
  | "accept-merge-pending"
  | "done"
  | "inconsistent";

export interface KartaItemGitState {
  binder: string;
  item: string;
  state: KartaItemRecoveryState;
  nextAction: string;
  integrationRef: string;
  integrationTip?: string;
  itemRef: string;
  itemTip?: string;
  worktree?: string;
  dirty: {
    staged: boolean;
    unstaged: boolean;
    untracked: boolean;
  };
  refs: {
    built?: string;
    failed?: string;
    done?: string;
    accepted?: string;
  };
  diagnostics: string[];
}

async function git(cwd: string, args: string[]): Promise<string> {
  try {
    const { stdout } = await exec("git", ["-C", cwd, ...args], {
      encoding: "utf8",
      maxBuffer: 4 * 1024 * 1024,
    });
    return stdout;
  } catch (error) {
    const stderr = (error as { stderr?: string }).stderr?.trim();
    throw new Error(stderr || `git ${args[0] ?? "command"} failed`);
  }
}

async function optionalRef(cwd: string, ref: string): Promise<string | undefined> {
  try {
    return (await git(cwd, ["rev-parse", "--verify", `${ref}^{commit}`])).trim();
  } catch {
    return undefined;
  }
}

async function isAncestor(cwd: string, ancestor: string, descendant: string): Promise<boolean> {
  try {
    await git(cwd, ["merge-base", "--is-ancestor", ancestor, descendant]);
    return true;
  } catch {
    return false;
  }
}

async function worktreeForBranch(cwd: string, branchRef: string): Promise<string | undefined> {
  const output = await git(cwd, ["worktree", "list", "--porcelain"]);
  let path: string | undefined;
  for (const line of output.split("\n")) {
    if (line.startsWith("worktree ")) path = line.slice("worktree ".length);
    if (line === `branch ${branchRef}`) return path;
    if (line === "") path = undefined;
  }
  return undefined;
}

async function dirtyState(worktree: string | undefined): Promise<KartaItemGitState["dirty"]> {
  if (!worktree) return { staged: false, unstaged: false, untracked: false };
  const [staged, unstaged, untracked] = await Promise.all([
    git(worktree, ["diff", "--cached", "--quiet", "--"]).then(
      () => false,
      () => true,
    ),
    git(worktree, ["diff", "--quiet", "--no-ext-diff", "--"]).then(
      () => false,
      () => true,
    ),
    git(worktree, ["ls-files", "--others", "--exclude-standard", "-z"]).then(
      (output) => output.length > 0,
    ),
  ]);
  return { staged, unstaged, untracked };
}

function result(
  base: Omit<KartaItemGitState, "state" | "nextAction">,
  state: KartaItemRecoveryState,
  nextAction: string,
): KartaItemGitState {
  return { ...base, state, nextAction };
}

export async function deriveItemGitState(
  cwd: string,
  binder: string,
  item: string,
): Promise<KartaItemGitState> {
  if (!IDENTIFIER.test(binder)) throw new Error(`Invalid Karta binder slug: ${binder}`);
  if (!IDENTIFIER.test(item)) throw new Error(`Invalid Karta item id: ${item}`);
  const integrationRef = `refs/heads/karta/${binder}/integration`;
  const itemRef = `refs/heads/karta/${binder}/item-${item}`;
  const markerRoot = `refs/karta/${binder}/item-${item}`;
  const [integrationTip, itemTip, built, failed, done, accepted, worktree] = await Promise.all([
    optionalRef(cwd, integrationRef),
    optionalRef(cwd, itemRef),
    optionalRef(cwd, `${markerRoot}/built`),
    optionalRef(cwd, `${markerRoot}/failed`),
    optionalRef(cwd, `${markerRoot}/done`),
    optionalRef(cwd, `${markerRoot}/accepted`),
    worktreeForBranch(cwd, itemRef),
  ]);
  const dirty = await dirtyState(worktree);
  const diagnostics: string[] = [];
  const base = {
    binder,
    item,
    integrationRef,
    integrationTip,
    itemRef,
    itemTip,
    worktree,
    dirty,
    refs: { built, failed, done, accepted },
    diagnostics,
  };

  if (!integrationTip) {
    diagnostics.push("integration branch is missing");
    return result(base, "inconsistent", "create or recover the binder integration branch");
  }
  if (!itemTip) {
    if (built || failed || done || accepted || worktree) {
      diagnostics.push("item markers or worktree exist without an item branch");
      return result(base, "inconsistent", "inspect and repair the orphaned item state");
    }
    return result(base, "not-started", "create the item branch and worktree from integration");
  }
  if (built && built !== itemTip) diagnostics.push("built ref does not match the item tip");
  if (failed && failed !== itemTip) diagnostics.push("failed ref does not match the item tip");
  if (accepted && accepted !== itemTip) diagnostics.push("accepted ref does not match the item tip");
  if (built && failed) diagnostics.push("built and failed refs both exist");
  if (accepted && !done) diagnostics.push("accepted exists without done");
  if (done && !(await isAncestor(cwd, done, integrationTip))) {
    diagnostics.push("done ref is not an ancestor of integration");
  }
  if (diagnostics.length > 0) {
    return result(base, "inconsistent", "inspect and repair contradictory Git markers");
  }

  const itemMerged = await isAncestor(cwd, itemTip, integrationTip);
  if (done) return result(base, "done", "skip this completed item");
  if (failed && itemMerged) {
    return result(
      base,
      "accept-merge-pending",
      "recover or revert the interrupted human-accept merge before continuing",
    );
  }
  if (built && itemMerged) {
    return result(base, "merged-unmarked", "revalidate the landed merge and write done ref-last");
  }
  if (failed) return result(base, "failed", "route the halted item to the existing human decision flow");
  if (built) return result(base, "built", "enqueue the committed item for serial integration");
  if (dirty.staged || dirty.unstaged || dirty.untracked) {
    return result(base, "worktree-dirty", "resume the existing item worktree without clobbering it");
  }
  const commitsAhead = Number((await git(cwd, ["rev-list", "--count", `${integrationTip}..${itemTip}`])).trim());
  if (commitsAhead > 0) {
    return result(
      base,
      "committed-unmarked",
      "re-run final checks and gates, then write built or failed ref-last",
    );
  }
  return result(base, "branch-only", "resume implementation in the existing clean worktree");
}
