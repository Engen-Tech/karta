#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


STYLE_JS = r"""(() => {
  const cs = getComputedStyle(document.documentElement);
  const tokens = {};
  Array.from(cs).filter(p => p.startsWith('--')).forEach(p => tokens[p] = cs.getPropertyValue(p).trim());
  const compact = text => (text || '').trim().replace(/\s+/g, ' ').slice(0, 120);
  const styleFor = el => {
    const s = getComputedStyle(el);
    return {
      tag: el.tagName,
      text: compact(el.textContent),
      fontSize: s.fontSize,
      fontWeight: s.fontWeight,
      color: s.color,
      backgroundColor: s.backgroundColor,
      borderRadius: s.borderRadius,
      padding: s.padding,
      fontFamily: s.fontFamily.slice(0, 80)
    };
  };
  return JSON.stringify({
    url: location.href,
    title: document.title,
    tokens,
    headings: Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6')).slice(0, 25).map(styleFor),
    buttons: Array.from(document.querySelectorAll('button')).slice(0, 30).map(styleFor),
    landmarks: Array.from(document.querySelectorAll('main,header,nav,aside,section,article')).slice(0, 30).map(styleFor)
  });
})()"""


AUTH_JS = r"""(() => {
  const text = (document.body && document.body.innerText || '').toLowerCase();
  const url = location.href.toLowerCase();
  const title = document.title.toLowerCase();
  const hasPassword = !!document.querySelector('input[type="password"], input[name*="password" i]');
  const authWords = ['sign in', 'signin', 'log in', 'login', 'authenticate', 'microsoft', 'entra'];
  const wordHit = authWords.some(w => text.includes(w) || url.includes(w) || title.includes(w));
  return JSON.stringify({
    url: location.href,
    title: document.title,
    hasPassword,
    wordHit,
    isLikelyAuth: hasPassword || wordHit,
    textSample: text.slice(0, 500)
  });
})()"""


PLAYWRIGHT_CLI_MISSING_HELP = (
    "playwright-cli is not available on PATH, so I can't capture the app/design "
    "for visual validation.\n"
    "\n"
    "To enable it (one-time):\n"
    "  1. npm install -g @playwright/cli@latest\n"
    "  2. playwright-cli install --skills   # adds its agent skill\n"
    "\n"
    "Docs: https://github.com/microsoft/playwright-cli\n"
    "Then re-run the validation and I'll pick up from here."
)


def resolve_playwright_command() -> list[str]:
    resolved = shutil.which("playwright-cli")
    if not resolved:
        raise SystemExit(PLAYWRIGHT_CLI_MISSING_HELP)
    return [resolved]


def run_cli(args: list[str], timeout: int = 30, check: bool = False) -> subprocess.CompletedProcess[str]:
    if args and args[0] == "playwright-cli":
        args = [*resolve_playwright_command(), *args[1:]]
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        raise RuntimeError(
            f"Command timed out ({timeout}s): {' '.join(args)}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        ) from exc
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(args)}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result


def cli(session: str, *args: str, timeout: int = 30, check: bool = False) -> subprocess.CompletedProcess[str]:
    return run_cli(["playwright-cli", f"-s={session}", *args], timeout=timeout, check=check)


def cli_stdout(session: str, *args: str, timeout: int = 30) -> str:
    return cli(session, *args, timeout=timeout, check=True).stdout


def eval_json(session: str, code: str, timeout: int = 30) -> tuple[dict[str, Any] | None, str]:
    result = run_cli(["playwright-cli", "--raw", f"-s={session}", "eval", code], timeout=timeout, check=True)
    raw = result.stdout.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        if isinstance(parsed, dict):
            return parsed, raw
        return None, raw
    except json.JSONDecodeError:
        return None, raw


def wait_for_any(session: str, selectors: list[str], timeout_ms: int) -> None:
    selector_array = json.dumps(selectors)
    code = (
        "async page => { "
        f"for (const sel of {selector_array}) {{ "
        f"try {{ await page.waitForSelector(sel, {{ timeout: {timeout_ms} }}); return sel; }} catch {{}} "
        "} "
        "throw new Error('No ready selector matched'); "
        "}"
    )
    cli(session, "run-code", code, timeout=max(10, len(selectors) * (timeout_ms // 1000 + 2)), check=True)


def click_text(session: str, text: str) -> None:
    code = f"async page => await page.getByText({json.dumps(text)}, {{ exact: false }}).first().click()"
    cli(session, "run-code", code, timeout=15, check=True)


def capture_target(
    session: str,
    name: str,
    url: str,
    out_dir: Path,
    ready_selectors: list[str],
    click_texts: list[str],
    detect_auth: bool,
) -> dict[str, Any]:
    cli(session, "goto", url, timeout=60, check=True)
    wait_for_any(session, ready_selectors, 5000)

    for text in click_texts:
        click_text(session, text)

    auth_info: dict[str, Any] | None = None
    if detect_auth:
        auth_info, _ = eval_json(session, AUTH_JS)
        if auth_info and auth_info.get("isLikelyAuth"):
            screenshot = out_dir / f"{name}.png"
            cli(session, "screenshot", f"--filename={screenshot}", timeout=30, check=True)
            return {
                "url": url,
                "health": "DEGRADED_AUTH",
                "auth": auth_info,
                "screenshot": str(screenshot),
                "dom_snapshot": None,
                "extracted_data": None,
                "console_errors": cli_stdout(session, "console", "error", timeout=10),
                "requests": cli_stdout(session, "requests", timeout=10),
            }

    screenshot = out_dir / f"{name}.png"
    snapshot = out_dir / f"{name}-snapshot.yaml"
    cli(session, "screenshot", f"--filename={screenshot}", timeout=30, check=True)
    cli(session, "snapshot", "--boxes", f"--filename={snapshot}", timeout=30, check=True)
    extracted, raw = eval_json(session, STYLE_JS)
    return {
        "url": url,
        "health": "OK",
        "auth": auth_info,
        "screenshot": str(screenshot),
        "dom_snapshot": str(snapshot),
        "extracted_data": extracted if extracted is not None else raw,
        "console_errors": cli_stdout(session, "console", "error", timeout=10),
        "requests": cli_stdout(session, "requests", timeout=10),
    }


def parse_viewport(value: str) -> tuple[int, int]:
    normalized = value.lower().replace(",", "x")
    parts = [p for p in normalized.split("x") if p]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("viewport must be WIDTHxHEIGHT, e.g. 1440x900")
    return int(parts[0]), int(parts[1])


def self_test() -> None:
    width, height = parse_viewport("1440x900")
    assert (width, height) == (1440, 900)
    sample = {"compare_ready": False, "app": {"health": "DEGRADED_AUTH"}}
    assert sample["app"]["health"] == "DEGRADED_AUTH"
    # CTA message content
    assert PLAYWRIGHT_CLI_MISSING_HELP.startswith("playwright-cli is not available on PATH")
    assert "npm install -g @playwright/cli@latest" in PLAYWRIGHT_CLI_MISSING_HELP
    assert "playwright-cli install --skills" in PLAYWRIGHT_CLI_MISSING_HELP
    assert "https://github.com/microsoft/playwright-cli" in PLAYWRIGHT_CLI_MISSING_HELP
    # resolve_playwright_command raises the CTA when the binary is absent
    original_which = shutil.which
    shutil.which = lambda _cmd: None
    try:
        raised = None
        try:
            resolve_playwright_command()
        except SystemExit as exc:
            raised = str(exc)
        assert raised == PLAYWRIGHT_CLI_MISSING_HELP
    finally:
        shutil.which = original_which
    print("capture_view self-test passed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture design and app views with playwright-cli.")
    parser.add_argument("--design-url")
    parser.add_argument("--app-url")
    parser.add_argument("--out", default="karta-validate-capture.json")
    parser.add_argument("--artifacts-dir", default=".karta-validate")
    parser.add_argument("--viewport", default="1440x900", type=parse_viewport)
    parser.add_argument("--session", default="karta-validate")
    parser.add_argument("--design-click-text", action="append", default=[])
    parser.add_argument("--app-click-text", action="append", default=[])
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return
    if not args.design_url or not args.app_url:
        parser.error("--design-url and --app-url are required unless --self-test is used")

    width, height = args.viewport
    out_path = Path(args.out).expanduser().resolve()
    artifacts_dir = Path(args.artifacts_dir).expanduser().resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "schema": "karta.validate.capture.v1",
        "viewport": {"width": width, "height": height},
        "design": None,
        "app": None,
        "APP_HEALTH": None,
        "STATUS": "capture_pending",
        "error": None,
        "compare_ready": False,
    }

    error_message: str | None = None
    try:
        resolve_playwright_command()
        cli(args.session, "open", timeout=30, check=True)
        cli(args.session, "resize", str(width), str(height), timeout=15, check=True)
        result["design"] = capture_target(
            args.session,
            "design",
            args.design_url,
            artifacts_dir,
            ["#root > *", "body > *"],
            args.design_click_text,
            detect_auth=False,
        )
        result["app"] = capture_target(
            args.session,
            "app",
            args.app_url,
            artifacts_dir,
            ["main", "#__next > *", "#root > *", "#app > *", "body > *"],
            args.app_click_text,
            detect_auth=True,
        )
        result["APP_HEALTH"] = result["app"]["health"]
        result["compare_ready"] = result["APP_HEALTH"] != "DEGRADED_AUTH"
        result["STATUS"] = "captured" if result["compare_ready"] else "blocked_auth"
    except Exception as exc:
        error_message = str(exc)
        result["STATUS"] = "error"
        result["error"] = error_message
    finally:
        try:
            cli(args.session, "close", timeout=15, check=True)
        except Exception as close_exc:
            close_message = str(close_exc)
            if error_message:
                result["close_error"] = close_message
            else:
                error_message = close_message
                result["STATUS"] = "error"
                result["error"] = close_message

    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if error_message:
        print(error_message, file=sys.stderr)
        print(json.dumps({"capture": str(out_path), "status": result["STATUS"], "error": error_message}))
        raise SystemExit(1)
    print(json.dumps({"capture": str(out_path), "compare_ready": result["compare_ready"]}))


if __name__ == "__main__":
    main()
