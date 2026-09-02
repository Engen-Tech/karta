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

const GRACE_POLL_MS = 50;

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
  // The grace is an escalation ceiling, not a fixed wait: poll for the group to die so a
  // cooperative process is reclaimed promptly, and only SIGKILL one that outlives the
  // whole window.
  const deadline = Date.now() + graceMs;
  while (processGroupAlive(pid)) {
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    await new Promise((resolve) => setTimeout(resolve, Math.min(GRACE_POLL_MS, remaining)));
  }
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

export type KartaProcessCheckpoint = (
  name: "owner-created" | "process-created",
  details: { binder?: string; pid?: number; label?: string },
) => void;

export class KartaProcessManager {
  readonly #lifecycles: LifecycleRegistry;
  readonly #processes = new Map<number, string>();
  readonly #graceMs: number;
  readonly #checkpoint: KartaProcessCheckpoint;

  constructor(
    lifecycles: LifecycleRegistry,
    graceMs = DEFAULT_GRACE_MS,
    checkpoint: KartaProcessCheckpoint = () => {},
  ) {
    this.#lifecycles = lifecycles;
    this.#graceMs = graceMs;
    this.#checkpoint = checkpoint;
  }

  createBinderOwner(cwd: string, binder: string): BinderLifecycleOwner {
    const id = this.#lifecycles.register({
      role: "delivery",
      cwd,
      label: binder,
      resource: { abort() {}, dispose() {} },
    });
    try {
      this.#checkpoint("owner-created", { binder });
    } catch (error) {
      this.#lifecycles.forget(id);
      throw error;
    }
    return { id, binder, cwd };
  }

  registerProcess(
    pid: number,
    options: {
      cwd: string;
      parentId: string;
      label: string;
      role?: "host-check" | "managed-process" | "env-server";
      // A long-lived role (a dev server whose graceful stop deserves a real window)
      // passes its own grace at the call site rather than churning the shared default
      // every other managed process relies on.
      graceMs?: number;
    },
  ): string {
    if (!Number.isInteger(pid) || pid <= 0) throw new Error("Karta managed process pid is invalid");
    if (this.#processes.has(pid)) throw new Error(`Karta process ${pid} is already managed`);
    const graceMs = options.graceMs ?? this.#graceMs;
    if (!Number.isInteger(graceMs) || graceMs < 0) throw new Error("Karta managed process graceMs is invalid");
    const id = this.#lifecycles.register({
      role: options.role ?? "managed-process",
      cwd: options.cwd,
      parentId: options.parentId,
      label: options.label,
      resource: {
        abort: () => stopProcessTree(pid, graceMs),
        dispose() {},
      },
    });
    this.#processes.set(pid, id);
    this.#checkpoint("process-created", { pid, label: options.label });
    return id;
  }

  forgetProcess(pid: number): void {
    const id = this.#processes.get(pid);
    if (!id) return;
    this.#lifecycles.forget(id);
    this.#processes.delete(pid);
  }

  // Non-vetoable teardown of a single managed process: abort its lifecycle (SIGTERM,
  // the registered grace, then SIGKILL of the whole group on POSIX) and stop tracking
  // it. Idempotent — a process already gone or never managed is a no-op — so an
  // owner's teardown path may call it on every exit without guarding.
  async stopProcess(pid: number): Promise<void> {
    const id = this.#processes.get(pid);
    if (!id) return;
    await this.#lifecycles.stop(id);
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
