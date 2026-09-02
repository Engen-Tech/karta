import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function modeProvider(extension: ExtensionAPI): void {
  const baseUrl = process.env.KARTA_MODE_PROVIDER_URL;
  if (!baseUrl) throw new Error("KARTA_MODE_PROVIDER_URL is required");
  extension.registerProvider("karta-mode", {
    name: "Karta mode fixture",
    baseUrl,
    apiKey: "$KARTA_MODE_PROVIDER_KEY",
    api: "openai-completions",
    models: [{
      id: "fixture",
      name: "Karta mode fixture",
      reasoning: false,
      input: ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 32_768,
      maxTokens: 2_048,
    }],
  });
}
