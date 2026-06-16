#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import json
import tempfile
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen


def iter_html_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.html"):
        try:
            depth = len(path.relative_to(root).parts)
        except ValueError:
            continue
        if depth <= 2 and "print" not in path.name.lower():
            files.append(path)
    return sorted(files, key=lambda p: str(p).lower())


def resolve_design_file(design_path: Path) -> Path:
    path = design_path.expanduser().resolve()
    if path.is_file():
        if path.suffix.lower() != ".html":
            raise SystemExit(f"Design path is not an HTML file: {path}")
        return path
    if not path.is_dir():
        raise SystemExit(f"Design path does not exist: {path}")

    html_files = iter_html_files(path)
    standalone = [p for p in html_files if "standalone" in p.name.lower()]
    candidates = standalone or html_files
    if not candidates:
        raise SystemExit(f"No design HTML files found at {path}")
    return candidates[0]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


def verify_url(url: str) -> int:
    try:
        with contextlib.closing(urlopen(url, timeout=5)) as response:
            return int(response.status)
    except HTTPError as exc:
        return int(exc.code)
    except URLError as exc:
        raise SystemExit(f"Design server did not respond at {url}: {exc}") from exc


def metadata_path(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    root = Path(tempfile.gettempdir()) / "karta-validate"
    root.mkdir(parents=True, exist_ok=True)
    return root / "design-server.json"


def run_server(design_file: Path, metadata_out: Path) -> None:
    handler = functools.partial(QuietHandler, directory=str(design_file.parent))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = int(server.server_address[1])
    design_url = f"http://127.0.0.1:{port}/{quote(design_file.name)}"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    status = verify_url(design_url)
    if status != 200:
        server.shutdown()
        raise SystemExit(f"Design page returned HTTP {status}: {design_url}")

    metadata = {
        "design_file": str(design_file),
        "design_dir": str(design_file.parent),
        "design_url": design_url,
        "host": "127.0.0.1",
        "port": port,
        "metadata": str(metadata_out),
    }
    metadata_out.parent.mkdir(parents=True, exist_ok=True)
    metadata_out.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata), flush=True)

    try:
        thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        html = root / "demo.standalone.html"
        html.write_text("<!doctype html><title>karta</title><div id='root'>ok</div>", encoding="utf-8")
        design_file = resolve_design_file(root)
        assert design_file == html.resolve()

        handler = functools.partial(QuietHandler, directory=str(root))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/{html.name}"
            assert verify_url(url) == 200
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print("serve_design self-test passed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve a Karta design HTML file on localhost.")
    parser.add_argument("--design-path", help="Design HTML file or directory containing one.")
    parser.add_argument("--metadata-out", help="Path for JSON metadata. Defaults to the OS temp dir.")
    parser.add_argument("--self-test", action="store_true", help="Run a local self-test and exit.")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return
    if not args.design_path:
        parser.error("--design-path is required unless --self-test is used")

    design_file = resolve_design_file(Path(args.design_path))
    run_server(design_file, metadata_path(args.metadata_out))


if __name__ == "__main__":
    main()
