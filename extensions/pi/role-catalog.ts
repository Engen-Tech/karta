import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { requirePackagePath } from "./package-paths.ts";

export type KartaRoleId =
  | "acceptance-gate"
  | "safety-gate"
  | "visual-gate"
  | "build-worker"
  | "doc-gardner"
  | "kaizen";

export type KartaRoleAuthority = "read-only" | "worktree-write" | "surface-write";

export type KartaCapability =
  | "evidence.read"
  | "checks.read"
  | "boundary.inspect"
  | "worktree.read"
  | "worktree.write"
  | "command.run"
  | "docs.write"
  | "packs.write";

interface RoleCatalogEntry {
  source: string;
  expectedName: string;
  authority: KartaRoleAuthority;
  capabilities: readonly KartaCapability[];
  outputSchema: "gate-verdict-v1" | "worker-result-v2" | "writer-result-v1";
}

const ROLE_CATALOG: Record<KartaRoleId, RoleCatalogEntry> = {
  "acceptance-gate": {
    source: "agents/karta-acceptance-reviewer.md",
    expectedName: "karta-acceptance-reviewer",
    authority: "read-only",
    capabilities: ["evidence.read", "checks.read"],
    outputSchema: "gate-verdict-v1",
  },
  "safety-gate": {
    source: "agents/karta-safety-auditor.md",
    expectedName: "karta-safety-auditor",
    authority: "read-only",
    capabilities: ["evidence.read", "boundary.inspect"],
    outputSchema: "gate-verdict-v1",
  },
  "visual-gate": {
    source: "agents/karta-design-reviewer.md",
    expectedName: "karta-design-reviewer",
    authority: "read-only",
    capabilities: ["evidence.read"],
    outputSchema: "gate-verdict-v1",
  },
  "build-worker": {
    source: "skills/karta-build/SKILL.md",
    expectedName: "karta-build",
    authority: "worktree-write",
    capabilities: ["worktree.read", "worktree.write", "command.run"],
    outputSchema: "worker-result-v2",
  },
  "doc-gardner": {
    source: "agents/karta-doc-gardner.md",
    expectedName: "karta-doc-gardner",
    authority: "surface-write",
    capabilities: ["worktree.read", "docs.write"],
    outputSchema: "writer-result-v1",
  },
  kaizen: {
    source: "agents/karta-kaizen.md",
    expectedName: "karta-kaizen",
    authority: "surface-write",
    capabilities: ["worktree.read", "packs.write"],
    outputSchema: "writer-result-v1",
  },
};

export interface KartaRoleDefinition extends RoleCatalogEntry {
  id: KartaRoleId;
  sourcePath: string;
  sourceHash: string;
  prompt: string;
  promptHash: string;
  definitionHash: string;
}

function hash(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

function parsePromptSource(source: string, path: string): { name: string; prompt: string } {
  if (!source.startsWith("---\n")) throw new Error(`Karta role source has no frontmatter: ${path}`);
  const end = source.indexOf("\n---\n", 4);
  if (end < 0) throw new Error(`Karta role source has unterminated frontmatter: ${path}`);
  const frontmatter = source.slice(4, end);
  const nameLine = frontmatter
    .split("\n")
    .find((line) => line.startsWith("name:"));
  const name = nameLine?.slice("name:".length).trim();
  if (!name) throw new Error(`Karta role source has no name: ${path}`);
  const prompt = source.slice(end + "\n---\n".length).trim();
  if (!prompt) throw new Error(`Karta role source has an empty prompt: ${path}`);
  return { name, prompt };
}

export function loadKartaRole(role: string): KartaRoleDefinition {
  if (!Object.hasOwn(ROLE_CATALOG, role)) throw new Error(`Unknown Karta role: ${role}`);
  const id = role as KartaRoleId;
  const entry = ROLE_CATALOG[id];
  const sourcePath = requirePackagePath(entry.source);
  const source = readFileSync(sourcePath, "utf8");
  const parsed = parsePromptSource(source, sourcePath);
  if (parsed.name !== entry.expectedName) {
    throw new Error(
      `Karta role '${id}' expected source name '${entry.expectedName}', found '${parsed.name}'`,
    );
  }
  const sourceHash = hash(source);
  const promptHash = hash(parsed.prompt);
  const definitionHash = hash(
    JSON.stringify({
      id,
      sourceHash,
      promptHash,
      authority: entry.authority,
      capabilities: entry.capabilities,
      outputSchema: entry.outputSchema,
    }),
  );
  return {
    id,
    ...entry,
    sourcePath,
    sourceHash,
    prompt: parsed.prompt,
    promptHash,
    definitionHash,
  };
}

export function listKartaRoles(): KartaRoleDefinition[] {
  return Object.keys(ROLE_CATALOG).map(loadKartaRole);
}
