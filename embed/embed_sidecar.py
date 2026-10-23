#!/usr/bin/env python3
"""Local embedding sidecar for vaultsearch.

Loads all-MiniLM-L6-v2 (384-dim) once and serves embeddings over loopback HTTP
so the Go service never needs a Python runtime in-process.

  POST /embed  {"texts": ["...", ...]}  ->  {"vectors": [[...], ...]}
  GET  /health -> {"ok": true, "dim": 384}

Binds 127.0.0.1:7433 only. $0, fully local.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

MODEL = os.environ.get(
    "EMBED_MODEL",
    os.path.expanduser(
        "~/workspace/goals/github-portfolio-linkedin-presence-buildout/"
        "hidden_files/builds/promptgate/models/all-MiniLM-L6-v2"),
)

from sentence_transformers import SentenceTransformer  # noqa: E402

_model = SentenceTransformer(MODEL)


def embed(texts):
    vecs = _model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [[float(x) for x in v] for v in vecs]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"ok": True, "dim": 384}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/embed":
            self.send_response(404)
            self.end_headers()
            return
        n = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(n) or b"{}")
        texts = payload.get("texts", [])
        body = json.dumps({"vectors": embed(texts)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.environ.get("EMBED_PORT", "7433"))
    print(f"embed sidecar on 127.0.0.1:{port} model={MODEL}", flush=True)
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
