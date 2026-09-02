import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function dynamicProvider(extension: ExtensionAPI): void {
  extension.registerProvider("phase0-dynamic", {
    name: "Phase 0 dynamic provider",
    baseUrl: "https://phase0.invalid/v1",
    apiKey: "$PHASE0_DYNAMIC_API_KEY",
    api: "openai-completions",
    models: [
      {
        id: "fixture",
        name: "Phase 0 fixture",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 4096,
        maxTokens: 256,
      },
    ],
  });
}
