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
const MAX_EVIDENCE_FILE_BYTES = 512 * 1024;
const MAX_EVIDENCE_FILES_BYTES = 2 * 1024 * 1024;
const MAX_EVIDENCE_CITATIONS = 8;
const DEFAULT_DIFF_PAGE = 30_000;
const MAX_DIFF_PAGE = 50_000;

interface BinderDocument {
  slug: string;
  sme?: string[];
  work_items: Array<Record<string, unknown> & { id: string }>;
  [key: string]: unknown;
}

export interface EvidenceChecklistItem {
  id: string;
  text: string;
  source: string;
}

export interface EvidencePack {
  id: string;
  source: "project" | "package";
  path: string;
  blob?: string;
  sha256: string;
  checklist: EvidenceChecklistItem[];
}

export type KartaEvidenceTargetKind = "candidate-tree" | "committed-tip" | "merge-tree";

export interface EvidenceFile {
  index: number;
  path: string;
  state: "present" | "deleted" | "missing";
  sourceTree: string;
  mode?: string;
  objectType?: string;
  blob?: string;
  bytes?: number;
  binary: boolean;
  content?: string;
  omitted?: "too-large" | "non-blob";
}

export interface EvidenceCitation extends EvidenceFile {
  locator: string;
}

export interface KartaCheckReceipt {
  schema: "karta-check-receipt-v1";
  targetTree: string;
  commandHash: string;
  cwd: string;
  status: "passed" | "failed";
  code: number;
  stdout: string;
  stderr: string;
  stdoutTruncated: boolean;
  stderrTruncated: boolean;
  durationMs: number;
}

export interface KartaCheckEvidence {
  status: "passed" | "failed" | "missing" | "not-required";
  targetTree: string;
  commandHash?: string;
  receipt?: KartaCheckReceipt;
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
    targetKind: KartaEvidenceTargetKind;
    targetTree: string;
  };
  diff: {
    format: "git-binary-patch";
    sha256: string;
    bytes: number;
    touchedPaths: string[];
    content: string;
  };
  checks: { oracle: KartaCheckEvidence };
  files: EvidenceFile[];
  citations: EvidenceCitation[];
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
  target?: "auto" | "candidate" | "committed" | "merge";
  checkReceipt?: KartaCheckReceipt;
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

export function hashCheckCommand(command: string): string {
  return sha256(command);
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

async function candidateTree(
  cwd: string,
  expectedBranch: string,
): Promise<{ kind: "candidate-tree"; tree: string } | undefined> {
  const branch = (await git(cwd, ["branch", "--show-current"])).trim();
  if (branch !== expectedBranch) return undefined;
  const unstaged = await gitOptional(cwd, ["diff", "--quiet", "--no-ext-diff", "--"]);
  const untracked = await git(cwd, ["ls-files", "--others", "--exclude-standard", "-z"]);
  if (unstaged === undefined || untracked.length > 0) {
    throw new Error(
      `Karta candidate '${expectedBranch}' has unstaged or untracked changes; stage the exact candidate before verification`,
    );
  }
  const tree = (await git(cwd, ["write-tree"])).trim();
  const headTree = (await git(cwd, ["rev-parse", "HEAD^{tree}"])).trim();
  return tree === headTree ? undefined : { kind: "candidate-tree", tree };
}

async function mergeTree(cwd: string, integrationTip: string, itemTip: string): Promise<string> {
  const output = await git(cwd, ["merge-tree", "--write-tree", integrationTip, itemTip]);
  const tree = output.split("\n", 1)[0].trim();
  if (!/^[a-f0-9]{40,64}$/.test(tree)) {
    throw new Error("Karta could not derive a clean proposed merge tree");
  }
  return tree;
}

async function resolveEvidenceTarget(
  cwd: string,
  expectedBranch: string,
  integrationTip: string,
  itemTip: string,
  requested: BuildEvidenceOptions["target"],
): Promise<{ kind: KartaEvidenceTargetKind; tree: string }> {
  if (requested === "merge") {
    return { kind: "merge-tree", tree: await mergeTree(cwd, integrationTip, itemTip) };
  }
  if (requested !== "committed") {
    const candidate = await candidateTree(cwd, expectedBranch);
    if (candidate) return candidate;
    if (requested === "candidate") {
      throw new Error(`Karta candidate '${expectedBranch}' has no staged tree distinct from HEAD`);
    }
  }
  return {
    kind: "committed-tip",
    tree: (await git(cwd, ["rev-parse", `${itemTip}^{tree}`])).trim(),
  };
}

async function treeFile(
  cwd: string,
  tree: string,
  path: string,
  index: number,
  state: EvidenceFile["state"],
): Promise<EvidenceFile> {
  const listing = await git(cwd, ["ls-tree", "-z", tree, "--", path]);
  if (!listing) {
    return { index, path, state: "missing", sourceTree: tree, binary: false };
  }
  const tab = listing.indexOf("\t");
  const metadata = listing.slice(0, tab).split(" ");
  const [mode, objectType, blob] = metadata;
  if (objectType !== "blob") {
    return {
      index,
      path,
      state,
      sourceTree: tree,
      mode,
      objectType,
      blob,
      binary: false,
      omitted: "non-blob",
    };
  }
  const bytes = Number((await git(cwd, ["cat-file", "-s", blob])).trim());
  if (!Number.isSafeInteger(bytes) || bytes < 0) {
    throw new Error(`Git returned an invalid blob size for evidence path: ${path}`);
  }
  if (bytes > MAX_EVIDENCE_FILE_BYTES) {
    return {
      index,
      path,
      state,
      sourceTree: tree,
      mode,
      objectType,
      blob,
      bytes,
      binary: false,
      omitted: "too-large",
    };
  }
  const content = await git(cwd, ["show", `${tree}:${path}`]);
  const binary = content.includes("\0");
  return {
    index,
    path,
    state,
    sourceTree: tree,
    mode,
    objectType,
    blob,
    bytes,
    binary,
    ...(binary ? {} : { content }),
  };
}

async function loadTouchedFiles(
  cwd: string,
  integrationTip: string,
  targetTree: string,
  paths: string[],
): Promise<EvidenceFile[]> {
  const files: EvidenceFile[] = [];
  let includedBytes = 0;
  for (const [index, path] of paths.entries()) {
    let file = await treeFile(cwd, targetTree, path, index, "present");
    if (file.state === "missing") {
      file = await treeFile(cwd, integrationTip, path, index, "deleted");
    }
    if (file.content !== undefined) {
      const nextBytes = Buffer.byteLength(file.content);
      if (includedBytes + nextBytes > MAX_EVIDENCE_FILES_BYTES) {
        file = { ...file, content: undefined, omitted: "too-large" };
      } else {
        includedBytes += nextBytes;
      }
    }
    files.push(file);
  }
  return files;
}

function citationRequests(diff: string): Array<{ path: string; locator: string }> {
  const requests: Array<{ path: string; locator: string }> = [];
  const seen = new Set<string>();
  for (const line of diff.split("\n")) {
    if (!line.startsWith("+") || line.startsWith("+++")) continue;
    const marker = line.match(/repo-rule:\s+([^\s:]+):([^\]\n;]+)/i);
    if (!marker) continue;
    const path = marker[1];
    const locator = marker[2].trim();
    validateGitPath(path);
    const key = `${path}\0${locator}`;
    if (!seen.has(key)) {
      seen.add(key);
      requests.push({ path, locator });
    }
  }
  if (requests.length > MAX_EVIDENCE_CITATIONS) {
    throw new Error(
      `Karta evidence contains ${requests.length} repo-rule citations; limit is ${MAX_EVIDENCE_CITATIONS}`,
    );
  }
  return requests;
}

async function loadCitations(
  cwd: string,
  integrationTip: string,
  targetTree: string,
  diff: string,
): Promise<EvidenceCitation[]> {
  const citations: EvidenceCitation[] = [];
  for (const [index, request] of citationRequests(diff).entries()) {
    let file = await treeFile(cwd, targetTree, request.path, index, "present");
    if (file.state === "missing") {
      file = await treeFile(cwd, integrationTip, request.path, index, "present");
    }
    citations.push({ ...file, locator: request.locator });
  }
  return citations;
}

const checklistCache = new Map<string, EvidenceChecklistItem[]>();

async function resolvePackChecklist(
  id: string,
  content: string,
): Promise<EvidenceChecklistItem[]> {
  const cacheKey = `${id}\0${sha256(content)}`;
  const cached = checklistCache.get(cacheKey);
  if (cached) return cached.map((item) => ({ ...item }));
  const root = await mkdtemp(join(tmpdir(), "karta-evidence-pack-"));
  const path = join(root, `${id}.md`);
  const environment = { ...process.env };
  delete environment.PYTHONHOME;
  delete environment.PYTHONPATH;
  environment.CLAUDE_PLUGIN_ROOT = requirePackagePath(".");
  environment.PYTHONNOUSERSITE = "1";
  environment.PYTHONSAFEPATH = "1";
  await writeFile(path, content);
  try {
    const { stdout } = await exec(
      "uv",
      [
        "run",
        "--script",
        requirePackagePath("skills/karta-kaizen/scripts/resolve_pack_checklist.py"),
        path,
      ],
      { encoding: "utf8", env: environment, timeout: 30_000, maxBuffer: 2 * 1024 * 1024 },
    );
    const checklist = JSON.parse(stdout) as unknown;
    if (
      !Array.isArray(checklist) ||
      !checklist.every(
        (item) =>
          item &&
          typeof item === "object" &&
          typeof (item as EvidenceChecklistItem).id === "string" &&
          typeof (item as EvidenceChecklistItem).text === "string" &&
          typeof (item as EvidenceChecklistItem).source === "string",
      )
    ) {
      throw new Error(`Karta pack '${id}' resolver returned an invalid checklist`);
    }
    const resolved = checklist as EvidenceChecklistItem[];
    checklistCache.set(cacheKey, resolved.map((item) => ({ ...item })));
    return resolved;
  } catch (error) {
    const output = `${(error as { stdout?: string }).stdout ?? ""}${(error as { stderr?: string }).stderr ?? ""}`.trim();
    throw new Error(output || `Karta pack '${id}' checklist resolution failed`);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

function checkEvidence(
  workItem: Record<string, unknown>,
  targetTree: string,
  receipt: KartaCheckReceipt | undefined,
): KartaCheckEvidence {
  const oracle = workItem.oracle;
  if (!oracle || typeof oracle !== "object" || Array.isArray(oracle)) {
    throw new Error("Karta work item has no valid oracle after binder validation");
  }
  const command = (oracle as Record<string, unknown>).command;
  if (command === undefined) return { status: "not-required", targetTree };
  if (typeof command !== "string" || !command.trim()) {
    throw new Error("Karta oracle command is invalid after binder validation");
  }
  const commandHash = hashCheckCommand(command);
  if (!receipt) return { status: "missing", targetTree, commandHash };
  if (
    receipt.schema !== "karta-check-receipt-v1" ||
    receipt.targetTree !== targetTree ||
    receipt.commandHash !== commandHash ||
    receipt.cwd !== ((oracle as Record<string, unknown>).cwd ?? ".") ||
    !["passed", "failed"].includes(receipt.status) ||
    !Number.isInteger(receipt.code) ||
    (receipt.status === "passed" && receipt.code !== 0) ||
    (receipt.status === "failed" && receipt.code === 0) ||
    typeof receipt.stdout !== "string" ||
    typeof receipt.stderr !== "string" ||
    typeof receipt.stdoutTruncated !== "boolean" ||
    typeof receipt.stderrTruncated !== "boolean" ||
    !Number.isFinite(receipt.durationMs) ||
    receipt.durationMs < 0 ||
    Buffer.byteLength(receipt.stdout) > 64 * 1024 ||
    Buffer.byteLength(receipt.stderr) > 64 * 1024
  ) {
    throw new Error("Karta oracle check receipt does not bind to the candidate tree and command");
  }
  return { status: receipt.status, targetTree, commandHash, receipt };
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
        checklist: await resolvePackChecklist(id, projectContent),
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
      checklist: await resolvePackChecklist(id, packageContent),
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
  const itemBranch = `karta/${options.binder}/item-${options.item}`;
  const itemRef = `refs/heads/${itemBranch}`;
  const [integrationTip, itemTip] = await Promise.all([
    git(repositoryRoot, ["rev-parse", "--verify", `${integrationRef}^{commit}`]),
    git(repositoryRoot, ["rev-parse", "--verify", `${itemRef}^{commit}`]),
  ]).then((values) => values.map((value) => value.trim()));
  const target = await resolveEvidenceTarget(
    repositoryRoot,
    itemBranch,
    integrationTip,
    itemTip,
    options.target ?? "auto",
  );
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
      integrationTip,
      target.tree,
      "--",
    ]),
    git(repositoryRoot, [
      "diff",
      "--name-only",
      "-z",
      "--no-ext-diff",
      integrationTip,
      target.tree,
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
  const [packs, files, citations] = await Promise.all([
    loadPacks(repositoryRoot, integrationTip, document.sme ?? []),
    loadTouchedFiles(repositoryRoot, integrationTip, target.tree, touchedPaths),
    loadCitations(repositoryRoot, integrationTip, target.tree, diffContent),
  ]);
  const checks = { oracle: checkEvidence(workItem, target.tree, options.checkReceipt) };
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
      targetKind: target.kind,
      targetTree: target.tree,
    },
    diff: {
      format: "git-binary-patch",
      sha256: sha256(diffContent),
      bytes: diffBytes,
      touchedPaths,
      content: diffContent,
    },
    checks,
    files,
    citations,
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
  if (evidenceGit.targetKind === "candidate-tree") {
    const expectedBranch = evidenceGit.itemRef.replace(/^refs\/heads\//, "");
    const candidate = await candidateTree(manifest.repositoryRoot, expectedBranch);
    if (!candidate || candidate.tree !== evidenceGit.targetTree) {
      throw new Error("Karta evidence is stale because the staged candidate tree changed");
    }
  } else if (evidenceGit.targetKind === "committed-tip") {
    const tree = (await git(manifest.repositoryRoot, ["rev-parse", `${itemTip}^{tree}`])).trim();
    if (tree !== evidenceGit.targetTree) {
      throw new Error("Karta evidence is stale because the committed item tree changed");
    }
  } else if (evidenceGit.targetKind === "merge-tree") {
    const tree = await mergeTree(manifest.repositoryRoot, integrationTip, itemTip);
    if (tree !== evidenceGit.targetTree) {
      throw new Error("Karta evidence is stale because the proposed merge tree changed");
    }
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
  Type.Object({
    action: Type.Literal("touchedFile"),
    index: Type.Integer({ minimum: 0 }),
    offset: Type.Optional(Type.Integer({ minimum: 0 })),
    limit: Type.Optional(Type.Integer({ minimum: 1, maximum: MAX_DIFF_PAGE })),
  }),
  Type.Object({
    action: Type.Literal("citation"),
    index: Type.Integer({ minimum: 0 }),
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
              files: manifest.payload.files.map(({ content: _content, ...metadata }) => metadata),
              citations: manifest.payload.citations.map(({ content: _content, ...metadata }) => metadata),
              packs: manifest.payload.packs.map(({ id, source, path, blob, sha256: packHash, checklist }) => ({
                id,
                source,
                path,
                blob,
                sha256: packHash,
                checklistItems: checklist.length,
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
          case "touchedFile":
          case "citation": {
            const entries =
              params.action === "touchedFile" ? manifest.payload.files : manifest.payload.citations;
            const entry = entries[params.index];
            if (!entry) throw new Error(`${params.action} index ${params.index} is not in this evidence manifest`);
            const content = entry.content ?? "";
            const offset = params.offset ?? 0;
            const limit = params.limit ?? DEFAULT_DIFF_PAGE;
            totalLength = content.length;
            value = { ...entry, content: content.slice(offset, offset + limit) };
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
