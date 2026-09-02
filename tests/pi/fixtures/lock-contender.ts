import { acquireDispatchLock } from "../../../extensions/pi/dispatch-lock.ts";

const [repo, binder, hold = "250"] = process.argv.slice(2);
if (!repo || !binder) throw new Error("usage: lock-contender.ts <repo> <binder> [hold-ms]");

try {
  const lease = await acquireDispatchLock(repo, binder);
  process.stdout.write("ACQUIRED\n");
  await new Promise((resolve) => setTimeout(resolve, Number(hold)));
  await lease.release();
  process.exitCode = 0;
} catch (error) {
  process.stdout.write(`LOCKED:${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 3;
}
