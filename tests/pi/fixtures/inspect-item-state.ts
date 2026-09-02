import { deriveItemGitState } from "../../../extensions/pi/git-state.ts";

const [cwd, binder, item] = process.argv.slice(2);
if (!cwd || !binder || !item) throw new Error("usage: inspect-item-state <cwd> <binder> <item>");
const state = await deriveItemGitState(cwd, binder, item);
process.stdout.write(`${JSON.stringify(state)}\n`);
