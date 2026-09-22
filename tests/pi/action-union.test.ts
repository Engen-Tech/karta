import assert from "node:assert/strict";
import test from "node:test";
import { Type, type TSchema } from "typebox";
import { Compile } from "typebox/compile";
import { ActionUnion } from "../../extensions/pi/action-union.ts";
import { ChildRegistry } from "../../extensions/pi/child-runtime.ts";
import { createKartaDispatchTool } from "../../extensions/pi/dispatch-tool.ts";
import { ScriptParameters } from "../../extensions/pi/script-tool.ts";

type ActionSchema = {
  type?: string;
  properties?: Record<string, unknown>;
  required?: string[];
  anyOf: Array<{ properties: Record<string, { const?: string }> }>;
};

function assertOpenAICompatibleRoot(schema: TSchema): void {
  const root = schema as unknown as ActionSchema;
  assert.equal(root.type, "object");
  assert.deepEqual(root.required, ["action"]);
  const memberKeys = new Set(root.anyOf.flatMap((member) => Object.keys(member.properties)));
  assert.deepEqual(Object.keys(root.properties ?? {}).sort(), [...memberKeys].sort());
  assert.deepEqual(
    (root.properties?.action as { enum: string[] }).enum,
    root.anyOf.map((member) => member.properties.action.const),
  );
}

test("an action union keeps strict per-action validation behind an object root", () => {
  const PathValue = Type.String({ minLength: 1, maxLength: 4096 });
  const schema = ActionUnion([
    Type.Object({ action: Type.Literal("detect"), root: Type.Optional(PathValue) }, { additionalProperties: false }),
    Type.Object({ action: Type.Literal("validate"), binder: PathValue }, { additionalProperties: false }),
    Type.Object(
      { action: Type.Literal("next"), binder: Type.Optional(Type.String({ minLength: 1, maxLength: 200 })) },
      { additionalProperties: false },
    ),
  ]);
  assertOpenAICompatibleRoot(schema);
  assert.equal(((schema as unknown as ActionSchema).properties?.binder as { anyOf: unknown[] }).anyOf.length, 2);

  const validator = Compile(schema);
  const cases: Array<[unknown, boolean]> = [
    [{ action: "detect" }, true],
    [{ action: "detect", root: "." }, true],
    [{ action: "validate", binder: "work.json" }, true],
    [{ action: "validate" }, false],
    [{ action: "detect", binder: "work.json" }, false],
    [{ action: "next", binder: "x".repeat(201) }, false],
    [{ action: "unknown" }, false],
    [{}, false],
  ];
  for (const [value, expected] of cases) {
    assert.equal(validator.Check(value), expected, JSON.stringify(value));
  }
});

test("karta_script and karta_dispatch expose an object root to OpenAI-compatible servers", () => {
  assertOpenAICompatibleRoot(ScriptParameters);
  const dispatch = createKartaDispatchTool(
    { ensure: async () => assert.fail("preflight is not exercised") },
    new ChildRegistry(),
  );
  assertOpenAICompatibleRoot(dispatch.parameters);
});
