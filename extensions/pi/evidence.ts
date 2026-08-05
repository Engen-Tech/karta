import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, relative, resolve, sep } from "node:path";
import { promisify } from "node:util";
import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import { Type, type Static } from "typebox";
import { requirePackagePath } from "./package-paths.ts";

const exec = promisify(execFile);
const IDENTIFIER = /^[a-z0-9][a-z0-9-]*$/;
const DEFAULT_MAX_DIFF_BYTES = 5 * 1024 * 1024;
const MAX_BINDER_BYTES = 2 * 1024 * 1024;
const MAX_PACK_BYTES = 1024 * 1024;
const DEFAULT_DIFF_PAGE = 30_000;
const MAX_DIFF_PAGE = 50_000;

interface BinderDocument {
  slug: string;
  sme?: string[];
  work_items: Array<Record<string, unknown> & { id: string }>;
  [key: string]: unknown;
}

export interface EvidencePack {
  id: string;
  source: "project" | "package";
  path: string;
  blob?: string;
  sha256: string;
  content: string;
}

export interface KartaEvidencePayload {
  binder: {
    slug: string;
    path: string;
    blob: string;
    sha256: string;
    document: BinderDocument;
  };
  workItem: Record<string, unknown> & { id: string };
  git: {
    integrationRef: string;
    integrationTip: string;
    itemRef: string;
    itemTip: string;
    mergeBase: string;
  };
  diff: {
    format: "git-binary-patch";
    sha256: string;
    bytes: number;
    touchedPaths: string[];
    content: string;
  };
  packs: EvidencePack[];
}

export interface KartaEvidenceManifest {
  schema: "karta-evidence-v1";
  generatedAt: string;
  repositoryRoot: string;
  evidenceHash: string;
  payload: KartaEvidencePayload;
}

export interface BuildEvidenceOptions {
  cwd: string;
  binder: string;
  item: string;
  maxDiffBytes?: number;
}

function sha256(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, child]) => [key, canonicalValue(child)]),
    );
  }
  return value;
}

export function canonicalJson(value: unknown): string {
  return JSON.stringify(canonicalValue(value));
}

export function hashEvidencePayload(payload: KartaEvidencePayload): string {
  return sha256(canonicalJson(payload));
}

async function git(cwd: string, args: string[]): Promise<string> {
  try {
    const { stdout } = await exec("git", ["-C", cwd, ...args], {
      encoding: "utf8",
      maxBuffer: 16 * 1024 * 1024,
    });
    return stdout;
  } catch (error) {
    const stderr = (error as { stderr?: string }).stderr?.trim();
    throw new Error(stderr || `git ${args[0] ?? "command"} failed`);
  }
}

async function gitOptional(cwd: string, args: string[]): Promise<string | undefined> {
  try {
    return await git(cwd, args);
  } catch {
    return undefined;
  }
}

function validateIdentifier(kind: string, value: string): void {
  if (!IDENTIFIER.test(value)) throw new Error(`Invalid Karta ${kind}: ${value}`);
}

async function validateBinderSource(raw: string, slug: string): Promise<void> {
  const root = await mkdtemp(join(tmpdir(), "karta-evidence-binder-"));
  const path = join(root, `${slug}.json`);
  const environment = { ...process.env };
  delete environment.PYTHONHOME;
  delete environment.PYTHONPATH;
  environment.PYTHONNOUSERSITE = "1";
  environment.PYTHONSAFEPATH = "1";
  await writeFile(path, raw);
  try {
    await exec(
      "uv",
      [
        "run",
        "--script",
        requirePackagePath("skills/karta-plan/scripts/validate_binder.py"),
        "--binder",
        path,
      ],
      { encoding: "utf8", env: environment, timeout: 30_000, maxBuffer: 2 * 1024 * 1024 },
    );
  } catch (error) {
    const output = `${(error as { stdout?: string }).stdout ?? ""}${(error as { stderr?: string }).stderr ?? ""}`.trim();
    throw new Error(output || `Karta binder '${slug}' failed package validation`);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

function parseBinder(raw: string, expectedSlug: string, item: string): {
  document: BinderDocument;
  workItem: BinderDocument["work_items"][number];
} {
  let document: unknown;
  try {
    document = JSON.parse(raw);
  } catch {
    throw new Error(`Karta binder '${expectedSlug}' is not valid JSON`);
  }
  if (!document || typeof document !== "object") {
    throw new Error(`Karta binder '${expectedSlug}' is not an object`);
  }
  const binder = document as BinderDocument;
  if (binder.slug !== expectedSlug || !Array.isArray(binder.work_items)) {
    throw new Error(`Karta binder '${expectedSlug}' has an invalid slug or work_items`);
  }
  const matches = binder.work_items.filter((candidate) => candidate?.id === item);
  if (matches.length !== 1) {
    throw new Error(`Karta binder '${expectedSlug}' must contain item '${item}' exactly once`);
  }
  if (binder.sme !== undefined) {
    if (!Array.isArray(binder.sme) || !binder.sme.every((id) => typeof id === "string" && IDENTIFIER.test(id))) {
      throw new Error(`Karta binder '${expectedSlug}' has invalid sme ids`);
    }
  }
  return { document: binder, workItem: matches[0] };
}

function validateGitPath(path: string): void {
  const normalized = path.replaceAll("\\", "/");
  if (
    !normalized ||
    normalized.startsWith("/") ||
    normalized.split("/").some((part) => part === "..")
  ) {
    throw new Error(`Git returned an unsafe evidence path: ${path}`);
  }
}

async function loadPacks(
  cwd: string,
  policyTip: string,
  ids: string[],
): Promise<EvidencePack[]> {
  const packs: EvidencePack[] = [];
  for (const id of ids) {
    const projectPath = `.karta/sme/${id}.md`;
    const projectContent = await gitOptional(cwd, ["show", `${policyTip}:${projectPath}`]);
    if (projectContent !== undefined) {
      const blob = (await git(cwd, ["rev-parse", `${policyTip}:${projectPath}`])).trim();
      if (Buffer.byteLength(projectContent) > MAX_PACK_BYTES) {
        throw new Error(`Karta project pack '${id}' exceeds ${MAX_PACK_BYTES} bytes`);
      }
      packs.push({
        id,
        source: "project",
        path: projectPath,
        blob,
        sha256: sha256(projectContent),
        content: projectContent,
      });
      continue;
    }
    const packagePath = requirePackagePath(`skills/karta-plan/references/sme/${id}.md`);
    const packageContent = await readFile(packagePath, "utf8");
    if (Buffer.byteLength(packageContent) > MAX_PACK_BYTES) {
      throw new Error(`Karta package pack '${id}' exceeds ${MAX_PACK_BYTES} bytes`);
    }
    packs.push({
      id,
      source: "package",
      path: relative(requirePackagePath("."), packagePath).split(sep).join("/"),
      sha256: sha256(packageContent),
      content: packageContent,
    });
  }
  return packs;
}

export async function buildKartaEvidence(
  options: BuildEvidenceOptions,
): Promise<KartaEvidenceManifest> {
  validateIdentifier("binder slug", options.binder);
  validateIdentifier("item id", options.item);
  const repositoryRoot = resolve(
    (await git(options.cwd, ["rev-parse", "--show-toplevel"])).trim(),
  );
  const integrationRef = `refs/heads/karta/${options.binder}/integration`;
  const itemRef = `refs/heads/karta/${options.binder}/item-${options.item}`;
  const [integrationTip, itemTip] = await Promise.all([
    git(repositoryRoot, ["rev-parse", "--verify", `${integrationRef}^{commit}`]),
    git(repositoryRoot, ["rev-parse", "--verify", `${itemRef}^{commit}`]),
  ]).then((values) => values.map((value) => value.trim()));
  const mergeBase = (await git(repositoryRoot, ["merge-base", integrationTip, itemTip])).trim();
  const binderPath = `.karta/binders/${options.binder}.json`;
  const binderRaw = await git(repositoryRoot, ["show", `${integrationTip}:${binderPath}`]);
  if (Buffer.byteLength(binderRaw) > MAX_BINDER_BYTES) {
    throw new Error(`Karta binder '${options.binder}' exceeds ${MAX_BINDER_BYTES} bytes`);
  }
  const parsedBinder = parseBinder(binderRaw, options.binder, options.item);
  await validateBinderSource(binderRaw, options.binder);
  const binderBlob = (await git(repositoryRoot, ["rev-parse", `${integrationTip}:${binderPath}`])).trim();
  const { document, workItem } = parsedBinder;
  const [diffContent, touchedRaw] = await Promise.all([
    git(repositoryRoot, [
      "diff",
      "--binary",
      "--no-color",
      "--no-ext-diff",
      `${integrationTip}..${itemTip}`,
      "--",
    ]),
    git(repositoryRoot, [
      "diff",
      "--name-only",
      "-z",
      "--no-ext-diff",
      `${integrationTip}..${itemTip}`,
      "--",
    ]),
  ]);
  const diffBytes = Buffer.byteLength(diffContent);
  const maxDiffBytes = options.maxDiffBytes ?? DEFAULT_MAX_DIFF_BYTES;
  if (diffBytes > maxDiffBytes) {
    throw new Error(`Karta evidence diff is ${diffBytes} bytes; limit is ${maxDiffBytes}`);
  }
  const touchedPaths = touchedRaw.split("\0").filter(Boolean).sort();
  touchedPaths.forEach(validateGitPath);
  const packs = await loadPacks(repositoryRoot, integrationTip, document.sme ?? []);
  const payload: KartaEvidencePayload = {
    binder: {
      slug: options.binder,
      path: binderPath,
      blob: binderBlob,
      sha256: sha256(binderRaw),
      document,
    },
    workItem,
    git: {
      integrationRef,
      integrationTip,
      itemRef,
      itemTip,
      mergeBase,
    },
    diff: {
      format: "git-binary-patch",
      sha256: sha256(diffContent),
      bytes: diffBytes,
      touchedPaths,
      content: diffContent,
    },
    packs,
  };
  return {
    schema: "karta-evidence-v1",
    generatedAt: new Date().toISOString(),
    repositoryRoot,
    evidenceHash: hashEvidencePayload(payload),
    payload,
  };
}

export function verifyEvidenceIntegrity(manifest: KartaEvidenceManifest): void {
  if (manifest.schema !== "karta-evidence-v1") throw new Error("Unknown Karta evidence schema");
  const actual = hashEvidencePayload(manifest.payload);
  if (actual !== manifest.evidenceHash) {
    throw new Error(`Karta evidence hash mismatch: expected ${manifest.evidenceHash}, found ${actual}`);
  }
}

export async function verifyEvidenceFreshness(manifest: KartaEvidenceManifest): Promise<void> {
  verifyEvidenceIntegrity(manifest);
  const { git: evidenceGit } = manifest.payload;
  const [integrationTip, itemTip] = await Promise.all([
    git(manifest.repositoryRoot, ["rev-parse", "--verify", `${evidenceGit.integrationRef}^{commit}`]),
    git(manifest.repositoryRoot, ["rev-parse", "--verify", `${evidenceGit.itemRef}^{commit}`]),
  ]).then((values) => values.map((value) => value.trim()));
  if (integrationTip !== evidenceGit.integrationTip || itemTip !== evidenceGit.itemTip) {
    throw new Error("Karta evidence is stale because a bound Git tip moved");
  }
}

const evidenceReadParameters = Type.Union([
  Type.Object({ action: Type.Literal("summary") }),
  Type.Object({ action: Type.Literal("binder") }),
  Type.Object({ action: Type.Literal("workItem") }),
  Type.Object({
    action: Type.Literal("diff"),
    offset: Type.Optional(Type.Integer({ minimum: 0 })),
    limit: Type.Optional(Type.Integer({ minimum: 1, maximum: MAX_DIFF_PAGE })),
  }),
  Type.Object({ action: Type.Literal("pack"), id: Type.String({ pattern: "^[a-z0-9][a-z0-9-]*$" }) }),
]);

type EvidenceReadParameters = Static<typeof evidenceReadParameters>;

interface EvidenceReadDetails {
  action: EvidenceReadParameters["action"];
  evidenceHash: string;
  totalLength?: number;
  nextOffset?: number;
}

export function createEvidenceReadTool(
  manifest: KartaEvidenceManifest,
): ToolDefinition<typeof evidenceReadParameters, EvidenceReadDetails> {
  verifyEvidenceIntegrity(manifest);
  return {
    name: "karta_evidence",
    label: "Karta evidence",
    description:
      "Read fixed, hash-bound Karta evidence. It cannot read arbitrary files, paths, refs, or commands.",
    parameters: evidenceReadParameters,
    async execute(_toolCallId, params) {
      try {
        let value: unknown;
        let totalLength: number | undefined;
        let nextOffset: number | undefined;
        switch (params.action) {
          case "summary":
            value = {
              schema: manifest.schema,
              evidenceHash: manifest.evidenceHash,
              binder: manifest.payload.binder.slug,
              item: manifest.payload.workItem.id,
              git: manifest.payload.git,
              diff: {
                sha256: manifest.payload.diff.sha256,
                bytes: manifest.payload.diff.bytes,
                touchedPaths: manifest.payload.diff.touchedPaths,
              },
              packs: manifest.payload.packs.map(({ id, source, path, blob, sha256: packHash }) => ({
                id,
                source,
                path,
                blob,
                sha256: packHash,
              })),
            };
            break;
          case "binder":
            value = manifest.payload.binder.document;
            break;
          case "workItem":
            value = manifest.payload.workItem;
            break;
          case "diff": {
            const offset = params.offset ?? 0;
            const limit = params.limit ?? DEFAULT_DIFF_PAGE;
            totalLength = manifest.payload.diff.content.length;
            value = manifest.payload.diff.content.slice(offset, offset + limit);
            if (offset + limit < totalLength) nextOffset = offset + limit;
            break;
          }
          case "pack": {
            const pack = manifest.payload.packs.find((candidate) => candidate.id === params.id);
            if (!pack) throw new Error(`Pack '${params.id}' is not in this evidence manifest`);
            value = pack;
            break;
          }
        }
        return {
          content: [{ type: "text", text: typeof value === "string" ? value : JSON.stringify(value, null, 2) }],
          details: {
            action: params.action,
            evidenceHash: manifest.evidenceHash,
            totalLength,
            nextOffset,
          },
          isError: false,
        };
      } catch (error) {
        return {
          content: [{ type: "text", text: error instanceof Error ? error.message : String(error) }],
          details: { action: params.action, evidenceHash: manifest.evidenceHash },
          isError: true,
        };
      }
    },
  };
}
