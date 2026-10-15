"""Serve dist at both / and /dpeindex/ without special headers or a backend."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import argparse


class Handler(SimpleHTTPRequestHandler):
    directory_root = Path(__file__).resolve().parents[1] / "dist"
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.directory_root), **kwargs)

    def do_GET(self):
        if self.path == "/dpeindex":
            self.send_response(301); self.send_header("Location", "/dpeindex/"); self.end_headers(); return
        if self.path.startswith("/dpeindex/"):
            self.path = self.path[len("/dpeindex"):]
        super().do_GET()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--directory", type=Path, default=Handler.directory_root)
    args = parser.parse_args()
    Handler.directory_root = args.directory.resolve()
    print(f"Static preview: http://localhost:{args.port}/dpeindex/", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
