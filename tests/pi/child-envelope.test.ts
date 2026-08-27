import assert from "node:assert/strict";
import test from "node:test";
import { parseJsonEnvelope } from "../../extensions/pi/child-envelope.ts";

const envelope = { schema: "karta-writer-result-v1", summary: "ok" };

test("parseJsonEnvelope reads plain, fenced, and prose-wrapped objects", () => {
  assert.deepEqual(parseJsonEnvelope(JSON.stringify(envelope), "writer"), envelope);
  assert.deepEqual(
    parseJsonEnvelope("```json\n" + JSON.stringify(envelope) + "\n```", "writer"),
    envelope,
  );
  assert.deepEqual(
    parseJsonEnvelope(`Here is my result:\n\n${JSON.stringify(envelope)}\n\nDone.`, "writer"),
    envelope,
  );
});

test("parseJsonEnvelope names the child role and a snippet when it cannot find an object", () => {
  assert.throws(
    () => parseJsonEnvelope("The docs are all correct, no drift found.", "writer"),
    /Karta writer returned malformed JSON.*The docs are all correct/,
  );
  assert.throws(() => parseJsonEnvelope("   ", "writer"), /Karta writer returned malformed JSON.*<empty>/);
});
