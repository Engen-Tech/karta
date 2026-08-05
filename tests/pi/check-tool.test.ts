import assert from "node:assert/strict";
import test from "node:test";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { createCheckEvidenceTool } from "../../extensions/pi/check-tool.ts";
import {
  hashEvidencePayload,
  type KartaCheckEvidence,
  type KartaEvidenceManifest,
  type KartaEvidencePayload,
} from "../../extensions/pi/evidence.ts";

function manifest(check: KartaCheckEvidence): KartaEvidenceManifest {
  const workItem = {
    id: "item-a",
    title: "Check fixture",
    summary: "Read a check receipt",
    oracle: { type: "unit", command: "npm test" },
  };
  const payload: KartaEvidencePayload = {
    binder: {
      slug: "demo",
      path: ".karta/binders/demo.json",
      blob: "a".repeat(40),
      sha256: "b".repeat(64),
      document: { slug: "demo", work_items: [workItem] },
    },
    workItem,
    git: {
      integrationRef: "refs/heads/karta/demo/integration",
      integrationTip: "a".repeat(40),
      itemRef: "refs/heads/karta/demo/item-item-a",
      itemTip: "c".repeat(40),
      mergeBase: "a".repeat(40),
      targetKind: "candidate-tree",
      targetTree: "d".repeat(40),
    },
    diff: {
      format: "git-binary-patch",
      sha256: "e".repeat(64),
      bytes: 0,
      touchedPaths: [],
      content: "",
    },
    checks: { oracle: check },
    files: [],
    citations: [],
    packs: [],
  };
  return {
    schema: "karta-evidence-v1",
    generatedAt: "2026-08-05T00:00:00.000Z",
    repositoryRoot: "/not-used",
    evidenceHash: hashEvidencePayload(payload),
    payload,
  };
}

test("check tool returns only the receipt already bound into evidence", async () => {
  const check: KartaCheckEvidence = {
    status: "passed",
    targetTree: "d".repeat(40),
    commandHash: "f".repeat(64),
    receipt: {
      schema: "karta-check-receipt-v1",
      targetTree: "d".repeat(40),
      commandHash: "f".repeat(64),
      cwd: ".",
      status: "passed",
      code: 0,
      stdout: "tests passed\n",
      stderr: "",
      stdoutTruncated: false,
      stderrTruncated: false,
      durationMs: 25,
    },
  };
  const evidence = manifest(check);
  const tool = createCheckEvidenceTool(evidence);
  const result = await tool.execute(
    "checks",
    { action: "summary" },
    undefined,
    undefined,
    {} as ExtensionContext,
  );
  assert.equal((result as { isError?: boolean }).isError, false);
  assert.equal(result.details?.evidenceHash, evidence.evidenceHash);
  assert.equal(result.details?.status, "passed");
  assert.match(result.content[0].type === "text" ? result.content[0].text : "", /tests passed/);
});

test("check tool reports missing evidence without gaining execution authority", async () => {
  const tool = createCheckEvidenceTool(
    manifest({
      status: "missing",
      targetTree: "d".repeat(40),
      commandHash: "f".repeat(64),
    }),
  );
  const schema = JSON.stringify(tool.parameters);
  for (const forbidden of ["command", "cwd", "path", "environment", "ref", "timeout"] as const) {
    assert.equal(schema.includes(`\"${forbidden}\"`), false, forbidden);
  }
  const result = await tool.execute(
    "checks",
    { action: "summary" },
    undefined,
    undefined,
    {} as ExtensionContext,
  );
  assert.equal(result.details?.status, "missing");
  assert.equal((result as { isError?: boolean }).isError, false);
});
