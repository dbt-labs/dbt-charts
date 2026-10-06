#!/usr/bin/env python3
"""
Simple playground server for markdown-svg.

Run with: python playground/server.py
Or: python -m playground.server

The server provides:
- A web UI with CodeMirror editor and live SVG preview
- REST API for rendering markdown to SVG
- Example markdown files to explore
"""

import json
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mdsvg import Style, render

PLAYGROUND_DIR = Path(__file__).parent
ROOT_DIR = PLAYGROUND_DIR.parent
EXAMPLES_DIR = ROOT_DIR / "examples"

# Style options that can be passed from the frontend
STYLE_OPTIONS = {
    "font_family",
    "mono_font_family",
    "base_font_size",
    "line_height",
    "text_color",
    "heading_color",
    "link_color",
    "link_underline",
    "code_color",
    "code_background",
    "blockquote_color",
    "blockquote_border_color",
    "h1_scale",
    "h2_scale",
    "h3_scale",
    "h4_scale",
    "h5_scale",
    "h6_scale",
    "heading_font_weight",
    "heading_margin_top",
    "heading_margin_bottom",
    "paragraph_spacing",
    "list_indent",
    "list_item_spacing",
    "code_block_padding",
    "code_block_border_radius",
    "code_block_overflow",
    "blockquote_padding",
    "blockquote_border_width",
    "table_border_color",
    "table_border_width",
    "table_border_dasharray",
    "table_border_visible",
    "table_header_background",
    "table_cell_padding",
    "hr_color",
    "hr_height",
    "hr_dasharray",
    "hr_visible",
    "char_width_ratio",
    "bold_char_width_ratio",
    "italic_char_width_ratio",
    "image_width",
    "image_height",
    "image_fallback_aspect_ratio",
    "image_enforce_aspect_ratio",
}


def build_style(data: dict[str, Any]) -> Style:
    """Build a Style object from request data."""
    style_kwargs = {k: v for k, v in data.items() if k in STYLE_OPTIONS}
    return Style(**style_kwargs)


def list_examples() -> list[dict[str, str]]:
    """List all example markdown files."""
    examples = []
    if EXAMPLES_DIR.exists():
        for path in sorted(EXAMPLES_DIR.glob("*.md")):
            name = path.stem.replace("-", " ").replace("_", " ").title()
            examples.append(
                {
                    "name": name,
                    "filename": path.name,
                }
            )
    return examples


def load_example(filename: str) -> str:
    """Load an example file content."""
    path = EXAMPLES_DIR / filename
    if path.exists() and path.suffix == ".md":
        return path.read_text()
    return ""


class PlaygroundHandler(SimpleHTTPRequestHandler):
    """HTTP handler for the playground."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Set directory to playground folder
        super().__init__(*args, directory=str(PLAYGROUND_DIR), **kwargs)

    def do_GET(self) -> None:
        """Handle GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/examples":
            self._send_json(list_examples())
        elif path.startswith("/api/example/"):
            filename = path.split("/")[-1]
            content = load_example(filename)
            if content:
                self._send_json({"content": content})
            else:
                self._send_error(404, "Example not found")
        elif path.startswith("/examples/"):
            # Serve files from the examples directory
            self._serve_file(ROOT_DIR / path.lstrip("/"))
        else:
            # Serve static files from playground
            super().do_GET()

    def do_POST(self) -> None:
        """Handle POST requests."""
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/render":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)

            try:
                data = json.loads(body)
                markdown = data.get("markdown", "")
                width = data.get("width", 600)
                padding = data.get("padding", 20)

                style = build_style(data)

                svg = render(
                    markdown,
                    width=width,
                    padding=padding,
                    style=style,
                )

                self._send_json({"svg": svg})
            except json.JSONDecodeError:
                self._send_error(400, "Invalid JSON")
            except Exception as e:  # noqa: BLE001
                self._send_error(500, str(e))
        else:
            self._send_error(404, "Not found")

    def _send_json(self, data: Any) -> None:
        """Send a JSON response."""
        body = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code: int, message: str) -> None:
        """Send an error response."""
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, file_path: Path) -> None:
        """Serve a static file from an arbitrary path."""
        if not file_path.exists() or not file_path.is_file():
            self._send_error(404, "File not found")
            return

        # Determine content type
        suffix = file_path.suffix.lower()
        content_types = {
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
            ".md": "text/markdown",
        }
        content_type = content_types.get(suffix, "application/octet-stream")

        content = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        """Log with color."""
        # Safely extract method from first arg (could be string or HTTPStatus)
        method = ""
        if args and isinstance(args[0], str):
            parts = args[0].split()
            method = parts[0] if parts else ""

        if method == "POST":
            color = "\033[33m"  # Yellow
        elif len(args) > 1 and "200" in str(args[1]):
            color = "\033[32m"  # Green
        else:
            color = "\033[0m"  # Default
        reset = "\033[0m"
        print(f"{color}{self.address_string()} - {format % args}{reset}")


def run_server(host: str = "localhost", port: int = 8765) -> None:
    """Run the playground server."""
    # Generate example SVGs for the gallery thumbnails
    import subprocess

    print("Generating example SVGs...")
    subprocess.run(
        [sys.executable, str(PLAYGROUND_DIR / "generate_svgs.py")], check=True
    )

    server_address = (host, port)
    httpd = HTTPServer(server_address, PlaygroundHandler)

    print(
        f"""
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║   🎨 markdown-svg Playground                              ║
║                                                           ║
║   Open in your browser:                                   ║
║   → http://{host}:{port}                               ║
║                                                           ║
║   Press Ctrl+C to stop                                    ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
"""
    )

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        httpd.shutdown()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the markdown-svg playground")
    parser.add_argument("--host", default="localhost", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind to")

    args = parser.parse_args()
    run_server(args.host, args.port)
