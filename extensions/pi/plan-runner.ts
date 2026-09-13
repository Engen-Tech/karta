/**
 * Karta plan runner — package-owned enforcement for the plan phase on Pi.
 *
 * On Claude Code and Codex the plan phase is carried by skill prose, with one
 * exception that matters: `validate_binder.py` must pass before a binder is
 * committed. Pi had no plan action at all, so every rule in the phase — the
 * validator, the cross-item shared-term check, and the "commit only on the
 * `commit` verb" rule — rested on the model's discipline. A binder is the input
 * to every later phase, so that is the one place a slip is expensive.
 *
 * Two rules are enforced here instead of described:
 *
 *   1. A binder cannot be committed unless it validates — `validate_binder.py`
 *      and `check_shared_terms.py` both have to pass, and the pack provenance
 *      classification rides along on the review card.
 *   2. The commit happens only on the human's own decision, taken at a host
 *      prompt. Worker text is never consulted, exactly as with the accept
 *      waiver. The caller cannot supply the verb.
 *
 * A set of binders commits together, in one commit, the way the skill requires.
 *
 * The survey action covers the half of Phase 1 the repository can answer from
 * facts, so a Pi session does not improvise stack detection.
 */

import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { promisify } from "node:util";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { resolveKartaScript, type KartaScriptAction } from "./script-catalog.ts";

const exec = promisify(execFile);
const MAX_OUTPUT = 8 * 1024 * 1024;
const SCRIPT_TIMEOUT = 120_000;
const BINDER_ID = /^[a-z0-9][a-z0-9-]*$/;
const BINDER_ROOT = ".karta/binders";
const ROUNDTABLE_ROOT = ".karta/roundtable/";

export const KARTA_PLAN_COMMIT_SUBJECT = (binder: string): string => `karta: commit binder ${binder}`;

export interface KartaPlanScriptRun {
  action: KartaScriptAction;
  code: number;
  stdout: string;
  stderr: string;
}

export interface KartaPlanSurveyResult {
  schema: "karta-plan-survey-v1";
  root: string;
  runs: KartaPlanScriptRun[];
}

export interface KartaPlanCommitResult {
  schema: "karta-plan-commit-v1";
  binder: string;
  binders: string[];
  paths: string[];
  status: "committed" | "blocked" | "cancelled";
  card: string;
  runs: KartaPlanScriptRun[];
  commit?: string;
  message: string;
}

function output(run: KartaPlanScriptRun): string {
  return `${run.stdout}${run.stderr}`.trim();
}

async function git(cwd: string, args: string[], allowFailure = false): Promise<string> {
  try {
    const { stdout } = await exec("git", args, { cwd, encoding: "utf8", maxBuffer: MAX_OUTPUT });
    return stdout.trim();
  } catch (error) {
    if (allowFailure) return "";
    const failure = error as { stdout?: string; stderr?: string; message?: string };
    throw new Error(
      `${failure.stdout ?? ""}${failure.stderr ?? ""}`.trim() || failure.message || "Karta git command failed",
    );
  }
}

/**
 * A binder commit is the plan of record plus the review record filed beside it,
 * and nothing else. A set commits together, so every binder in the set belongs
 * to the same commit. Anything a pathspec or a stale index would sweep in makes
 * the commit more than its stated contents, so it is refused rather than
 * trimmed.
 */
function isCommittablePath(repoPath: string, binderPaths: string[]): boolean {
  return binderPaths.includes(repoPath) || repoPath.startsWith(ROUNDTABLE_ROOT);
}

interface BinderCardFacts {
  slug: string;
  title: string;
  summary: string;
  items: number;
  after: string[];
  sme: string[];
}

function cardFacts(slug: string, raw: string): BinderCardFacts {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error(`Karta binder '${slug}' is not valid JSON`);
  }
  if (typeof parsed !== "object" || parsed === null) {
    throw new Error(`Karta binder '${slug}' is not a JSON object`);
  }
  const document = parsed as Record<string, unknown>;
  const declared = typeof document.slug === "string" ? document.slug : "";
  if (declared !== slug) {
    throw new Error(`Karta binder '${slug}' declares slug '${declared || "(none)"}'`);
  }
  const workItems = Array.isArray(document.work_items) ? document.work_items : [];
  const after = Array.isArray(document.after)
    ? document.after.filter((value): value is string => typeof value === "string")
    : [];
  const sme = Array.isArray(document.sme)
    ? document.sme.filter((value): value is string => typeof value === "string")
    : [];
  return {
    slug,
    title: typeof document.title === "string" && document.title.trim() ? document.title.trim() : slug,
    summary: typeof document.summary === "string" ? document.summary.trim() : "",
    items: workItems.length,
    after,
    sme,
  };
}

function buildCard(
  facts: BinderCardFacts[],
  sme: string[],
  validation: string,
  provenance: string,
): string {
  const set = facts.length > 1;
  const lines: string[] = [
    set
      ? `Binder set — ${facts.length} binders, committed together:`
      : `Binder: ${facts[0].title} (${facts[0].slug})`,
  ];
  for (const binder of facts) {
    const shape = binder.after.length ? `after ${binder.after.join(", ")}` : "no `after` edge";
    if (set) {
      lines.push(`  - ${binder.slug} — ${binder.title} (${binder.items} work items, ${shape})`);
      lines.push(`    ${binder.summary || "(no summary recorded)"}`);
    } else {
      lines.push(`Goal: ${binder.summary || "(no summary recorded)"}`);
      lines.push(`Work items: ${binder.items}`);
      lines.push(
        `Shape: ${
          binder.after.length ? `a set — this binder runs after ${binder.after.join(", ")}` : "one binder — no `after` edge"
        }`,
      );
    }
  }
  lines.push(
    `Experts applied (sme): ${sme.length ? sme.join(", ") : "none"}`,
    "",
    "Validation passed. Recorded escapes:",
    validation || "(the validator printed no escape summary)",
    "",
    "Stack-pack provenance:",
    provenance || "(no .karta/sme/ packs to classify)",
  );
  return lines.join("\n");
}

export class KartaPlanRunner {
  async #script(
    ctx: ExtensionContext,
    action: KartaScriptAction,
    args: string[],
  ): Promise<KartaPlanScriptRun> {
    const script = resolveKartaScript(action);
    const environment = { ...process.env };
    delete environment.PYTHONHOME;
    delete environment.PYTHONPATH;
    environment.PYTHONNOUSERSITE = "1";
    environment.PYTHONSAFEPATH = "1";
    try {
      const { stdout, stderr } = await exec("uv", ["run", "--script", script, ...args], {
        cwd: ctx.cwd,
        encoding: "utf8",
        env: environment,
        timeout: SCRIPT_TIMEOUT,
        maxBuffer: MAX_OUTPUT,
      });
      return { action, code: 0, stdout, stderr };
    } catch (error) {
      const failure = error as { code?: number; stdout?: string; stderr?: string; message?: string };
      return {
        action,
        code: typeof failure.code === "number" ? failure.code : 1,
        stdout: failure.stdout ?? "",
        stderr: failure.stderr ?? failure.message ?? "",
      };
    }
  }

  async survey(ctx: ExtensionContext): Promise<KartaPlanSurveyResult> {
    const detectStack = await this.#script(ctx, "detectStack", [ctx.cwd]);
    if (detectStack.code !== 0) {
      throw new Error(`Karta stack detection failed:\n${output(detectStack)}`);
    }
    const checkPackProvenance = await this.#script(ctx, "checkPackProvenance", [ctx.cwd]);
    return {
      schema: "karta-plan-survey-v1",
      root: ctx.cwd,
      runs: [detectStack, checkPackProvenance],
    };
  }

  async commit(
    ctx: ExtensionContext,
    binder: string,
    set: string[] = [],
  ): Promise<KartaPlanCommitResult> {
    const slugs = [binder, ...set];
    for (const slug of slugs) {
      if (!BINDER_ID.test(slug)) throw new Error(`Invalid Karta binder slug: ${slug}`);
    }
    if (new Set(slugs).size !== slugs.length) {
      throw new Error("A Karta binder set cannot repeat a slug");
    }
    const root = ctx.cwd;
    const paths = slugs.map((slug) => `${BINDER_ROOT}/${slug}.json`);
    for (const path of paths) {
      if (!existsSync(join(root, path))) {
        throw new Error(`Karta has no draft binder at ${path} to commit`);
      }
    }

    const runs: KartaPlanScriptRun[] = [];
    const escapeBlocks: string[] = [];
    for (const [index, slug] of slugs.entries()) {
      const path = paths[index];
      const validation = await this.#script(ctx, "validateBinder", ["--binder", path]);
      runs.push(validation);
      if (validation.code !== 0) {
        throw new Error(`Karta binder '${slug}' failed validation; fix it and re-commit:\n${output(validation)}`);
      }
      const terms = await this.#script(ctx, "checkSharedTerms", ["--binder", path, root]);
      runs.push(terms);
      if (terms.code !== 0) {
        throw new Error(`Karta binder '${slug}' failed its shared-term check:\n${output(terms)}`);
      }
      escapeBlocks.push(slugs.length > 1 ? `--- ${slug} ---\n${output(validation)}` : output(validation));
    }
    const provenance = await this.#script(ctx, "checkPackProvenance", [root]);
    runs.push(provenance);

    const facts: BinderCardFacts[] = [];
    for (const [index, slug] of slugs.entries()) {
      facts.push(cardFacts(slug, await readFile(join(root, paths[index]), "utf8")));
    }
    const sme = [...new Set(facts.flatMap((binder) => binder.sme))];
    const card = buildCard(facts, sme, escapeBlocks.join("\n\n"), output(provenance));

    // Nothing may be staged except the plan of record and its review record.
    const strayBefore = (await git(root, ["diff", "--cached", "--name-only"], true))
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line !== "" && !isCommittablePath(line, paths));
    if (strayBefore.length > 0) {
      throw new Error(
        `Karta refuses to commit a binder with unrelated changes staged: ${strayBefore.join(", ")}`,
      );
    }

    if (!ctx.hasUI) {
      return {
        schema: "karta-plan-commit-v1",
        binder,
        binders: slugs,
        paths,
        status: "blocked",
        card,
        runs,
        message: `Binder '${binder}' validated and is ready; committing needs a human at a prompt.`,
      };
    }

    const reviewed = await ctx.ui.confirm(`Review binder '${binder}' before committing`, card);
    if (!reviewed) {
      return {
        schema: "karta-plan-commit-v1",
        binder,
        binders: slugs,
        paths,
        status: "cancelled",
        card,
        runs,
        message: "The binder was not committed: the review was declined.",
      };
    }

    // The verb is the human's. It never comes from the model, and an absent
    // answer is a refusal rather than a default.
    const verb = await ctx.ui.select(`Commit binder '${binder}'?`, ["commit", "cancel"]);
    if (verb !== "commit") {
      return {
        schema: "karta-plan-commit-v1",
        binder,
        binders: slugs,
        paths,
        status: "cancelled",
        card,
        runs,
        message: "The binder was not committed: the `commit` verb was not given.",
      };
    }

    await git(root, ["add", "--", ...paths]);
    const staged = (await git(root, ["diff", "--cached", "--name-only"], true))
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line !== "");
    for (const path of paths) {
      if (!staged.includes(path)) throw new Error(`Karta could not stage ${path} for commit`);
    }
    const stray = staged.filter((line) => !isCommittablePath(line, paths));
    if (stray.length > 0) {
      throw new Error(`Karta refuses to commit a binder with unrelated changes staged: ${stray.join(", ")}`);
    }

    const subject = KARTA_PLAN_COMMIT_SUBJECT(binder);
    await git(root, ["commit", "-m", subject]);
    const commit = await git(root, ["rev-parse", "HEAD"]);
    return {
      schema: "karta-plan-commit-v1",
      binder,
      binders: slugs,
      paths,
      status: "committed",
      card,
      runs,
      commit,
      message: `Committed binder '${binder}' as ${commit.slice(0, 12)}.`,
    };
  }
}
