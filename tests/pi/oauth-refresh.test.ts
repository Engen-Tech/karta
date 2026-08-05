import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { ModelRuntime } from "@earendil-works/pi-coding-agent";

const PROVIDER = "karta-oauth-fixture";

interface OAuthCredential {
  type: "oauth";
  access: string;
  refresh: string;
  expires: number;
  [key: string]: unknown;
}

type Credential = OAuthCredential | { type: "api_key"; key?: string };

class SerializedCredentialStore {
  #credential: Credential | undefined;
  #chain = Promise.resolve();

  constructor(credential: Credential) {
    this.#credential = credential;
  }

  async read(providerId: string): Promise<Credential | undefined> {
    return providerId === PROVIDER ? this.#credential : undefined;
  }

  async list(): Promise<Array<{ providerId: string; type: Credential["type"] }>> {
    return this.#credential ? [{ providerId: PROVIDER, type: this.#credential.type }] : [];
  }

  async modify(
    providerId: string,
    change: (current: Credential | undefined) => Promise<Credential | undefined>,
  ): Promise<Credential | undefined> {
    let resolveResult: ((credential: Credential | undefined) => void) | undefined;
    let rejectResult: ((error: unknown) => void) | undefined;
    const result = new Promise<Credential | undefined>((resolve, reject) => {
      resolveResult = resolve;
      rejectResult = reject;
    });
    this.#chain = this.#chain.then(async () => {
      try {
        const next = await change(providerId === PROVIDER ? this.#credential : undefined);
        if (next) this.#credential = next;
        resolveResult?.(providerId === PROVIDER ? this.#credential : undefined);
      } catch (error) {
        rejectResult?.(error);
      }
    });
    return result;
  }

  async delete(providerId: string): Promise<void> {
    if (providerId === PROVIDER) this.#credential = undefined;
  }

  expire(): void {
    if (this.#credential?.type === "oauth") this.#credential.expires = Date.now() - 1;
  }
}

function registerOAuthFixture(runtime: ModelRuntime, onRefresh: () => Promise<void>): void {
  runtime.registerProvider(PROVIDER, {
    name: "Karta OAuth fixture",
    baseUrl: "https://oauth-fixture.invalid/v1",
    api: "openai-completions",
    models: [
      {
        id: "model",
        name: "Karta OAuth model",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 4096,
        maxTokens: 256,
      },
    ],
    oauth: {
      name: "Karta OAuth fixture",
      async login() {
        throw new Error("login is not part of the refresh fixture");
      },
      async refreshToken(credentials) {
        await onRefresh();
        return {
          access: "refreshed-access",
          refresh: `${credentials.refresh}-rotated`,
          expires: Date.now() + 60 * 60 * 1000,
        };
      },
      getApiKey(credentials) {
        return credentials.access;
      },
    },
  });
}

function model(runtime: ModelRuntime) {
  const selected = runtime.getModel(PROVIDER, "model");
  assert.ok(selected);
  return selected;
}

test("active child runtimes refresh once after crossing an OAuth expiry boundary", async () => {
  const credentials = new SerializedCredentialStore({
    type: "oauth",
    access: "initial-access",
    refresh: "initial-refresh",
    expires: Date.now() + 10 * 60 * 1000,
  });
  const [left, right] = await Promise.all([
    ModelRuntime.create({ credentials, allowModelNetwork: false }),
    ModelRuntime.create({ credentials, allowModelNetwork: false }),
  ]);
  let refreshes = 0;
  const onRefresh = async () => {
    refreshes += 1;
    await new Promise((resolve) => setTimeout(resolve, 30));
  };
  registerOAuthFixture(left, onRefresh);
  registerOAuthFixture(right, onRefresh);
  const leftModel = model(left);
  const rightModel = model(right);

  assert.equal((await left.getAuth(leftModel))?.auth.apiKey, "initial-access");
  credentials.expire();
  const [leftAuth, rightAuth] = await Promise.all([
    left.getAuth(leftModel),
    right.getAuth(rightModel),
  ]);
  assert.equal(leftAuth?.auth.apiKey, "refreshed-access");
  assert.equal(rightAuth?.auth.apiKey, "refreshed-access");
  assert.equal(refreshes, 1);
});

test("file-backed OAuth refresh coalesces across independently created runtimes", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-oauth-refresh-"));
  const authPath = join(root, "auth.json");
  let refreshes = 0;
  await writeFile(
    authPath,
    JSON.stringify({
      [PROVIDER]: {
        type: "oauth",
        access: "expired-access",
        refresh: "initial-refresh",
        expires: Date.now() - 1,
      },
    }),
  );
  try {
    const [left, right] = await Promise.all([
      ModelRuntime.create({ authPath, allowModelNetwork: false }),
      ModelRuntime.create({ authPath, allowModelNetwork: false }),
    ]);
    const onRefresh = async () => {
      refreshes += 1;
      await new Promise((resolve) => setTimeout(resolve, 30));
    };
    registerOAuthFixture(left, onRefresh);
    registerOAuthFixture(right, onRefresh);
    const [leftAuth, rightAuth] = await Promise.all([
      left.getAuth(model(left)),
      right.getAuth(model(right)),
    ]);
    assert.equal(leftAuth?.auth.apiKey, "refreshed-access");
    assert.equal(rightAuth?.auth.apiKey, "refreshed-access");
    assert.equal(refreshes, 1);

    const stored = JSON.parse(await readFile(authPath, "utf8"));
    assert.equal(stored[PROVIDER].access, "refreshed-access");
    assert.equal(stored[PROVIDER].refresh, "initial-refresh-rotated");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
