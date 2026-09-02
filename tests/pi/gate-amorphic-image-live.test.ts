import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  ModelRegistry,
  ModelRuntime,
  defineTool,
  type ExtensionContext,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { createGateChildSession } from "../../extensions/pi/child-runtime.ts";

const ENABLED = process.env.KARTA_LIVE_AMORPHIC_IMAGE === "1";
const PROVIDER = process.env.KARTA_LIVE_AMORPHIC_PROVIDER ?? "amorphic";
const MODEL = process.env.KARTA_LIVE_AMORPHIC_MODEL ?? "claude-opus-5";
const IMAGE = fileURLToPath(new URL("../../docs/images/icon.png", import.meta.url));

test(
  "an isolated Amorphic gate child receives and reads a real image attachment",
  { skip: !ENABLED, timeout: 5 * 60_000 },
  async () => {
    const runtime = await ModelRuntime.create({ allowModelNetwork: false });
    const model = runtime.getModel(PROVIDER, MODEL);
    assert.ok(model, `missing live image model ${PROVIDER}/${MODEL}`);
    assert.ok(model.input.includes("image"), `${PROVIDER}/${MODEL} does not advertise image input`);
    const modelRegistry = new ModelRegistry(runtime);
    const ctx = {
      cwd: fileURLToPath(new URL("../..", import.meta.url)),
      model,
      modelRegistry,
      thinkingLevel: "minimal",
    } as unknown as ExtensionContext;
    const evidenceTool = defineTool({
      name: "karta_image_probe_evidence",
      label: "Image probe evidence",
      description: "Return the expected visual facts for the image probe.",
      parameters: Type.Object({}),
      async execute() {
        return {
          content: [{
            type: "text" as const,
            text: "Expected: a folded map, a gold star, and a yellow route.",
          }],
          details: {},
        };
      },
    });
    const { session, report } = await createGateChildSession(
      ctx,
      "Inspect the attached image directly. Do not call tools. Reply with exactly MAP_STAR_IMAGE_OK only if you can see a folded map, a gold star, and a yellow route; otherwise reply IMAGE_FAILED.",
      [evidenceTool],
      ctx.cwd,
    );
    try {
      const data = (await readFile(IMAGE)).toString("base64");
      await session.prompt("Inspect the image now.", {
        images: [{ type: "image", data, mimeType: "image/jpeg" }],
      });
      assert.equal(session.getLastAssistantText()?.trim(), "MAP_STAR_IMAGE_OK");
      assert.equal(report.provider, PROVIDER);
      assert.equal(report.model, MODEL);
      assert.equal(report.exactModelResolved, true);
      assert.equal(report.childAuthConfigured, true);
    } finally {
      session.dispose();
    }
  },
);
