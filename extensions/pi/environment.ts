import { execFile } from "node:child_process";
import { promisify } from "node:util";

const exec = promisify(execFile);
const MAX_CONFIG_BYTES = 64 * 1024;

// A project may declare, in .karta/environment.json, commands the host runs in each
// fresh check worktree before floor and oracle checks:
//   - `setup`: one provisioning command (install deps into a gitignored directory).
//     It exists because git worktrees do not share a gitignored dependency directory
//     (node_modules, .venv, target, ...): a build worker installs deps as a side
//     effect of self-checking, but the host's disposable proposed-integration and
//     post-wave worktrees get none.
//   - `preflight`: one cheap precondition probe (e.g. `docker info`) run BEFORE setup
//     and the floor. When it fails, the host halts cleanly instead of running the real
//     floor command into a wall of opaque tool errors (a DB-backed suite failing deep
//     inside pytest because the Docker daemon is unreachable). `on_unavailable` carries
//     the remediation text surfaced on that halt — the precondition the binder already
//     knows about, said once, up front, actionably.
// The declaration is stack-agnostic (the project names its own commands) and opt-in
// (absent means no setup and no preflight). It is read from a COMMITTED blob — the
// delivery's integration ref — never the mutable working tree: a build worker could
// poison a working-tree copy, but the integration ref carries only gate-approved,
// merged content, so the host never runs an unreviewed command.
export interface KartaEnvironmentConfig {
  setup?: string;
  preflight?: string;
  onUnavailable?: string;
}

const ALLOWED_KEYS = new Set(["setup", "preflight", "on_unavailable"]);

function requireCommand(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim() || value.length > 4_096) {
    throw new Error(
      `Karta .karta/environment.json ${field} must be a non-empty string under 4096 chars`,
    );
  }
  return value;
}

export async function readEnvironmentConfig(
  worktree: string,
  integrationRef: string,
): Promise<KartaEnvironmentConfig | undefined> {
  let raw: string;
  try {
    const { stdout } = await exec(
      "git",
      ["-C", worktree, "show", `${integrationRef}:.karta/environment.json`],
      { encoding: "utf8", maxBuffer: MAX_CONFIG_BYTES },
    );
    raw = stdout;
  } catch (error) {
    // `git show` fails with a recognizable message when the ref or the path is absent
    // — that is the opt-out. Any other failure (repository corruption, permissions, a
    // blob overflowing maxBuffer) must fail closed rather than silently skip setup.
    const stderr = ((error as { stderr?: string }).stderr ?? "").trim();
    if (/does not exist|exists on disk, but not in|invalid object name|unknown revision/i.test(stderr)) {
      return undefined;
    }
    throw new Error(
      `Karta could not read .karta/environment.json from ${integrationRef}: ${
        stderr || (error as Error).message
      }`,
    );
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
  const record = value as Record<string, unknown>;
  const unknown = Object.keys(record).filter((key) => !ALLOWED_KEYS.has(key));
  if (unknown.length > 0) {
    throw new Error(`Karta .karta/environment.json has unknown keys: ${unknown.sort().join(", ")}`);
  }
  const config: KartaEnvironmentConfig = {};
  if (record.setup !== undefined) config.setup = requireCommand(record.setup, "setup");
  if (record.preflight !== undefined) config.preflight = requireCommand(record.preflight, "preflight");
  if (record.on_unavailable !== undefined) {
    config.onUnavailable = requireCommand(record.on_unavailable, "on_unavailable");
  }
  return config;
}

export async function readEnvironmentSetup(
  worktree: string,
  integrationRef: string,
): Promise<string | undefined> {
  return (await readEnvironmentConfig(worktree, integrationRef))?.setup;
}
