"""
Playground module for markdown-svg.

This provides a simple web-based playground for experimenting with markdown-svg.
Run it with: just markdown_svg play
Or: python -m mdsvg.playground
"""

import json
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List

from .renderer import render
from .style import Style

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
}


def get_playground_dir() -> Path:
    """Get the playground directory path (dev-only; not shipped in the wheel).

    ``pyproject.toml``'s wheel target packages only ``src/mdsvg`` — the
    ``playground/`` directory never installs, so this only ever resolves
    against a source checkout.
    """
    return Path(__file__).parent.parent.parent / "playground"


def build_style(data: Dict[str, Any]) -> Style:
    """Build a Style object from request data."""
    style_kwargs = {k: v for k, v in data.items() if k in STYLE_OPTIONS}
    return Style(**style_kwargs)


def list_examples(examples_dir: Path) -> List[Dict[str, str]]:
    """List all example markdown files."""
    examples: List[Dict[str, str]] = []
    if examples_dir.exists():
        for path in sorted(examples_dir.glob("*.md")):
            name = path.stem.replace("-", " ").replace("_", " ").title()
            examples.append(
                {
                    "name": name,
                    "filename": path.name,
                }
            )
    return examples


def load_example(examples_dir: Path, filename: str) -> str:
    """Load an example file content."""
    path = examples_dir / filename
    if path.exists() and path.suffix == ".md":
        return path.read_text()
    return ""


class PlaygroundHandler(SimpleHTTPRequestHandler):
    """HTTP handler for the playground."""

    playground_dir: Path
    examples_dir: Path

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Set directory to playground folder
        super().__init__(*args, directory=str(self.playground_dir), **kwargs)

    def do_GET(self) -> None:
        """Handle GET requests."""
        from urllib.parse import urlparse

        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/examples":
            self._send_json(list_examples(self.examples_dir))
        elif path.startswith("/api/example/"):
            filename = path.split("/")[-1]
            content = load_example(self.examples_dir, filename)
            if content:
                self._send_json({"content": content})
            else:
                self._send_error(404, "Example not found")
        else:
            # Serve static files
            super().do_GET()

    def do_POST(self) -> None:
        """Handle POST requests."""
        from urllib.parse import urlparse

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

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        """Log with color."""
        method = args[0].split()[0] if args else ""
        if method == "POST":
            color = "\033[33m"  # Yellow
        elif "200" in str(args[1]) if len(args) > 1 else False:
            color = "\033[32m"  # Green
        else:
            color = "\033[0m"  # Default
        reset = "\033[0m"
        print(f"{color}{self.address_string()} - {format % args}{reset}")


def run_server(host: str = "localhost", port: int = 8765) -> None:
    """Run the playground server."""
    playground_dir = get_playground_dir()
    examples_dir = playground_dir / "examples"

    if not playground_dir.exists() or not (playground_dir / "index.html").exists():
        print(
            """
╔═══════════════════════════════════════════════════════════════════╗
║                                                                   ║
║   Playground files not found!                                     ║
║                                                                   ║
║   The playground is only available when running from source.      ║
║                                                                   ║
║   To use the playground:                                          ║
║                                                                   ║
║   1. Clone the repository:                                        ║
║      git clone https://github.com/davefowler/markdown-svg         ║
║                                                                   ║
║   2. Run the playground:                                          ║
║      cd markdown-svg                                              ║
║      python playground/server.py                                  ║
║                                                                   ║
╚═══════════════════════════════════════════════════════════════════╝
"""
        )
        sys.exit(1)

    # Set class variables for the handler
    PlaygroundHandler.playground_dir = playground_dir
    PlaygroundHandler.examples_dir = examples_dir

    server_address = (host, port)
    httpd = HTTPServer(server_address, PlaygroundHandler)

    print(
        f"""
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║   🎨 markdown-svg Playground                              ║
║                                                           ║
║   Open in your browser:                                   ║
║   → http://{host}:{port:<5}                             ║
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


def main() -> None:
    """Main entry point for the playground."""
    import argparse

    parser = argparse.ArgumentParser(description="Run the markdown-svg playground")
    parser.add_argument("--host", default="localhost", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind to")

    args = parser.parse_args()
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
