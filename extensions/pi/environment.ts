import { execFile } from "node:child_process";
import { promisify } from "node:util";

const exec = promisify(execFile);
const MAX_CONFIG_BYTES = 64 * 1024;

// A project may declare, in .karta/environment.json, one setup command the host
// runs in each fresh check worktree before floor and oracle checks. It exists
// because git worktrees do not share a gitignored dependency directory (node_modules,
// .venv, target, ...): a build worker installs deps as a side effect of self-checking,
// but the host's disposable proposed-integration and post-wave worktrees get none.
// The declaration is stack-agnostic (the project names its own installer) and opt-in
// (absent means no setup). It is read from a COMMITTED blob — the delivery's
// integration ref — never the mutable working tree: a build worker could poison a
// working-tree copy, but the integration ref carries only gate-approved, merged
// content, so the host never runs an unreviewed command.
interface EnvironmentConfig {
  setup?: string;
}

export async function readEnvironmentSetup(
  worktree: string,
  integrationRef: string,
): Promise<string | undefined> {
  let raw: string;
  try {
    const { stdout } = await exec(
      "git",
      ["-C", worktree, "show", `${integrationRef}:.karta/environment.json`],
      { encoding: "utf8", maxBuffer: MAX_CONFIG_BYTES },
    );
    raw = stdout;
  } catch {
    // The ref or the path is absent (or the blob is oversized): no declared setup.
    return undefined;
  }
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new Error("Karta .karta/environment.json is not valid JSON");
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Karta .karta/environment.json must be a JSON object");
  }
  const unknown = Object.keys(value as Record<string, unknown>).filter((key) => key !== "setup");
  if (unknown.length > 0) {
    throw new Error(`Karta .karta/environment.json has unknown keys: ${unknown.sort().join(", ")}`);
  }
  const { setup } = value as EnvironmentConfig;
  if (setup === undefined) return undefined;
  if (typeof setup !== "string" || !setup.trim() || setup.length > 4_096) {
    throw new Error("Karta .karta/environment.json setup must be a non-empty string under 4096 chars");
  }
  return setup;
}
