import { spawn } from "node:child_process";
import { LifecycleRegistry } from "./lifecycle-registry.ts";

const DEFAULT_GRACE_MS = 1_000;

async function taskkill(pid: number): Promise<void> {
  await new Promise<void>((resolve) => {
    const child = spawn("taskkill", ["/pid", String(pid), "/t", "/f"], {
      shell: false,
      stdio: "ignore",
      windowsHide: true,
    });
    child.once("error", () => resolve());
    child.once("close", () => resolve());
  });
}

function processGroupAlive(pid: number): boolean {
  try {
    process.kill(-pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code !== "ESRCH";
  }
}

async function stopProcessTree(pid: number, graceMs: number): Promise<void> {
  if (process.platform === "win32") {
    await taskkill(pid);
    return;
  }
  try {
    process.kill(-pid, "SIGTERM");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ESRCH") return;
    throw error;
  }
  if (!processGroupAlive(pid)) return;
  await new Promise((resolve) => setTimeout(resolve, graceMs));
  if (!processGroupAlive(pid)) return;
  try {
    process.kill(-pid, "SIGKILL");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
  }
}

export interface BinderLifecycleOwner {
  id: string;
  binder: string;
  cwd: string;
}

export class KartaProcessManager {
  readonly #lifecycles: LifecycleRegistry;
  readonly #processes = new Map<number, string>();
  readonly #graceMs: number;

  constructor(lifecycles: LifecycleRegistry, graceMs = DEFAULT_GRACE_MS) {
    this.#lifecycles = lifecycles;
    this.#graceMs = graceMs;
  }

  createBinderOwner(cwd: string, binder: string): BinderLifecycleOwner {
    const id = this.#lifecycles.register({
      role: "delivery",
      cwd,
      label: binder,
      resource: { abort() {}, dispose() {} },
    });
    return { id, binder, cwd };
  }

  registerProcess(
    pid: number,
    options: { cwd: string; parentId: string; label: string; role?: "host-check" | "managed-process" },
  ): string {
    if (!Number.isInteger(pid) || pid <= 0) throw new Error("Karta managed process pid is invalid");
    if (this.#processes.has(pid)) throw new Error(`Karta process ${pid} is already managed`);
    const id = this.#lifecycles.register({
      role: options.role ?? "managed-process",
      cwd: options.cwd,
      parentId: options.parentId,
      label: options.label,
      resource: {
        abort: () => stopProcessTree(pid, this.#graceMs),
        dispose() {},
      },
    });
    this.#processes.set(pid, id);
    return id;
  }

  forgetProcess(pid: number): void {
    const id = this.#processes.get(pid);
    if (!id) return;
    this.#lifecycles.forget(id);
    this.#processes.delete(pid);
  }

  async stopOwner(owner: BinderLifecycleOwner): Promise<void> {
    await this.#lifecycles.stop(owner.id);
    const active = new Set(this.#lifecycles.snapshot().map((record) => record.id));
    for (const [pid, id] of this.#processes) {
      if (!active.has(id)) this.#processes.delete(pid);
    }
  }

  get size(): number {
    return this.#processes.size;
  }
}
