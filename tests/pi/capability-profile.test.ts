import assert from "node:assert/strict";
import test from "node:test";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { inspectBoundaries } from "../../extensions/pi/boundary-inspector.ts";
import { createGateCapabilityProfile } from "../../extensions/pi/capability-profile.ts";
import {
  hashEvidencePayload,
  type KartaEvidenceManifest,
  type KartaEvidencePayload,
} from "../../extensions/pi/evidence.ts";

const TOOL_CONTEXT = {} as ExtensionContext;

function manifest(): KartaEvidenceManifest {
  const workItem = {
    id: "item-a",
    title: "Change authentication",
    summary: "Add token handling",
    touches: ["src/**"],
    contract: { token: "string" },
    shared_resources: ["authentication"],
    surface: { flagged: true, signals: ["contract-mutation", "sensitive-zone"] },
    oracle: { type: "unit", assertions: ["token is checked"] },
  };
  const payload: KartaEvidencePayload = {
    binder: {
      slug: "demo",
      path: ".karta/binders/demo.json",
      blob: "a".repeat(40),
      sha256: "b".repeat(64),
      document: {
        slug: "demo",
        sme: ["minimalism"],
        work_items: [
          workItem,
          {
            id: "item-b",
            title: "Dependency work",
            summary: "Change dependencies",
            touches: ["package.json"],
            oracle: { type: "unit" },
          },
        ],
      },
    },
    workItem,
    git: {
      integrationRef: "refs/heads/karta/demo/integration",
      integrationTip: "a".repeat(40),
      itemRef: "refs/heads/karta/demo/item-item-a",
      itemTip: "c".repeat(40),
      mergeBase: "a".repeat(40),
      targetKind: "committed-tip",
      targetTree: "c".repeat(40),
    },
    diff: {
      format: "git-binary-patch",
      sha256: "d".repeat(64),
      bytes: 100,
      touchedPaths: ["package.json", "src/auth/token.ts"],
      content:
        "diff --git a/src/auth/token.ts b/src/auth/token.ts\n+delete token forcibly\n+// KARTA-SME-OVERRIDE(min.1): compatibility\n",
    },
    checks: {
      manifest: { status: "not-required", targetTree: "c".repeat(40) },
    },
    files: [],
    citations: [],
    packs: [
      {
        id: "minimalism",
        source: "package",
        path: "skills/karta-plan/references/sme/minimalism.md",
        sha256: "e".repeat(64),
        dependencies: [],
        checklist: [{ id: "min.1", text: "Keep it small.", source: "minimalism.md" }],
      },
    ],
  };
  return {
    schema: "karta-evidence-v2",
    generatedAt: "2026-08-05T00:00:00.000Z",
    repositoryRoot: "/not-used-by-boundary-profile",
    evidenceHash: hashEvidencePayload(payload),
    payload,
  };
}

test("boundary inspection derives fixed cues without deciding the verdict", () => {
  const evidence = manifest();
  const inspection = inspectBoundaries(evidence);
  assert.equal(inspection.evidenceHash, evidence.evidenceHash);
  assert.deepEqual(inspection.undeclaredTouchedPaths, ["package.json"]);
  assert.deepEqual(inspection.overlappingWorkItems, [{ item: "item-b", paths: ["package.json"] }]);
  assert.deepEqual(inspection.sensitivePathCues, ["src/auth/token.ts"]);
  assert.deepEqual(inspection.dependencyChangeCues, ["package.json"]);
  assert.match(inspection.destructiveLineCues[0], /delete token/);
  assert.equal(inspection.overrideMarkers[0].rule, "min.1");
  assert.deepEqual(inspection.declaredBoundary.contract, { token: "string" });
});

test("acceptance and safety profiles expose exactly two role-owned read-only tools", () => {
  const evidence = manifest();
  const acceptance = createGateCapabilityProfile("acceptance-gate", evidence);
  const safety = createGateCapabilityProfile("safety-gate", evidence);
  assert.deepEqual(acceptance.toolNames, ["karta_evidence", "karta_checks"]);
  assert.deepEqual(safety.toolNames, ["karta_evidence", "karta_boundary"]);
  assert.match(acceptance.profileHash, /^[a-f0-9]{64}$/);
  assert.match(safety.profileHash, /^[a-f0-9]{64}$/);
  assert.notEqual(acceptance.profileHash, safety.profileHash);
  for (const profile of [acceptance, safety]) {
    assert.equal(profile.role.authority, "read-only");
    assert.equal(profile.toolNames.includes("bash"), false);
    assert.equal(profile.toolNames.includes("write"), false);
    assert.equal(profile.toolNames.includes("edit"), false);
    assert.equal(profile.toolNames.includes("karta_script"), false);
  }
});

test("the visual gate exposes only the read-only evidence tool, never the boundary tool", () => {
  const evidence = manifest();
  const visual = createGateCapabilityProfile("visual-gate", evidence);
  // The visual gate declares only evidence.read, so its role-owned tool is the
  // evidence reader itself — it must not fall through to the boundary inspector.
  assert.deepEqual(visual.toolNames, ["karta_evidence"]);
  assert.equal(visual.toolNames.includes("karta_boundary"), false);
  assert.equal(visual.toolNames.includes("karta_checks"), false);
  assert.equal(visual.role.authority, "read-only");
  assert.equal(visual.role.outputSchema, "gate-verdict-v1");
  assert.equal(visual.toolNames.includes("bash"), false);
  assert.equal(visual.toolNames.includes("write"), false);
  assert.equal(visual.toolNames.includes("edit"), false);
  assert.match(visual.profileHash, /^[a-f0-9]{64}$/);
  const acceptance = createGateCapabilityProfile("acceptance-gate", evidence);
  const safety = createGateCapabilityProfile("safety-gate", evidence);
  assert.notEqual(visual.profileHash, acceptance.profileHash);
  assert.notEqual(visual.profileHash, safety.profileHash);
});

test("gate capability schemas contain no caller-selected authority", async () => {
  const evidence = manifest();
  for (const role of ["acceptance-gate", "safety-gate", "visual-gate"] as const) {
    const profile = createGateCapabilityProfile(role, evidence);
    for (const tool of profile.tools) {
      const schema = JSON.stringify(tool.parameters);
      for (const forbidden of ["command", "cwd", "path", "ref", "prompt", "provider", "model", "tool"] as const) {
        assert.equal(schema.includes(`\"${forbidden}\"`), false, `${tool.name}:${forbidden}`);
      }
    }
  }

  const safety = createGateCapabilityProfile("safety-gate", evidence);
  const boundary = safety.tools.find((tool) => tool.name === "karta_boundary");
  assert.ok(boundary);
  const result = await boundary.execute(
    "boundary",
    { action: "inspect" },
    undefined,
    undefined,
    TOOL_CONTEXT,
  );
  assert.equal((result as { isError?: boolean }).isError, false);
  assert.match(result.content[0].type === "text" ? result.content[0].text : "", /undeclaredTouchedPaths/);
});
