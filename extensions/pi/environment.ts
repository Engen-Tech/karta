import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { promisify } from "node:util";

const exec = promisify(execFile);

// A project may declare, in .karta/environment.json, one setup command the host
// runs in every fresh check worktree before floor and oracle checks. It exists
// because git worktrees do not share a gitignored dependency directory (node_modules,
// .venv, target, ...): a build worker installs deps as a side effect of self-checking,
// but the host's disposable proposed-integration and post-wave worktrees get none.
// This declaration is stack-agnostic — the project names its own installer — and
// opt-in: absent file means no setup, exactly as before.
interface EnvironmentConfig {
  setup?: string;
}

async function repoRoot(cwd: string): Promise<string> {
  const { stdout } = await exec("git", ["-C", cwd, "rev-parse", "--show-toplevel"], {
    encoding: "utf8",
  });
  return stdout.trim();
}

export async function readEnvironmentSetup(cwd: string): Promise<string | undefined> {
  let raw: string;
  try {
    raw = await readFile(join(await repoRoot(cwd), ".karta", "environment.json"), "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw error;
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
  const keys = Object.keys(value as Record<string, unknown>);
  const unknown = keys.filter((key) => key !== "setup");
  if (unknown.length > 0) {
    throw new Error(`Karta .karta/environment.json has unknown keys: ${unknown.sort().join(", ")}`);
  }
  const { setup } = value as EnvironmentConfig;
  if (setup === undefined) return undefined;
  if (typeof setup !== "string" || !setup.trim()) {
    throw new Error("Karta .karta/environment.json setup must be a non-empty string");
  }
  return setup;
}
