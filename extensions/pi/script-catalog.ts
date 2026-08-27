import { requirePackagePath } from "./package-paths.ts";

export const KARTA_SCRIPT_PATHS = {
  captureView: "skills/karta-validate/scripts/capture_view.py",
  checkPackProvenance: "skills/karta-plan/scripts/check_pack_provenance.py",
  checkSharedTerms: "skills/karta-plan/scripts/check_shared_terms.py",
  detectStack: "skills/karta-plan/scripts/detect_stack.py",
  diffCapture: "skills/karta-validate/scripts/diff_capture.py",
  kartaNext: "skills/karta-status/scripts/karta_next.py",
  resolvePackChecklist: "skills/karta-kaizen/scripts/resolve_pack_checklist.py",
  scanSecrets: "skills/karta-build/scripts/scan_secrets.py",
  serveDesign: "skills/karta-validate/scripts/serve_design.py",
  serveStatus: "skills/karta-status/scripts/serve_status.py",
  validateBinder: "skills/karta-plan/scripts/validate_binder.py",
  validatePacks: "skills/karta-kaizen/scripts/validate_packs.py",
} as const;

export type KartaScriptAction = keyof typeof KARTA_SCRIPT_PATHS;

export function resolveKartaScript(action: KartaScriptAction): string {
  return requirePackagePath(KARTA_SCRIPT_PATHS[action]);
}
