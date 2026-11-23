"""Loopback HTTP service for internal support triage."""

import argparse
import json
import sqlite3
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from uuid import uuid4

from triage.pipeline import MODEL, PROMPT_VERSION, Ollama, load_articles, triage

EVENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    model_digest TEXT,
    elapsed_ms REAL NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    category TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(database):
    return closing(sqlite3.connect(database, timeout=5))


def serve(database, port, mode, ollama_url):
    database.parent.mkdir(parents=True, exist_ok=True)
    with connect(database) as connection:
        connection.executescript(EVENT_SCHEMA)

    model = Ollama(ollama_url) if mode == "v1" else None
    articles = load_articles()
    try:
        digest = model.digest() if model else None
    except (URLError, OSError, ValueError, KeyError, TypeError):
        digest = None

    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self.respond(200, {
                    "version": mode,
                    "model": MODEL if model else None,
                    "model_digest": digest,
                    "prompt_version": PROMPT_VERSION if model else None,
                })
            else:
                self.respond(404, {"error": "not_found"})

        def do_POST(self):
            if self.path != "/triage":
                self.respond(404, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    raise ValueError
                body = json.loads(self.rfile.read(length))
                ticket = body["ticket"]
                if not isinstance(ticket, str) or not 1 <= len(ticket.strip()) <= 1200:
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                self.respond(400, {"error": "expected a ticket of 1–1200 characters"})
                return

            run_id = uuid4().hex
            start = time.perf_counter()
            result = triage(ticket, articles, model)
            elapsed = (time.perf_counter() - start) * 1000
            with connect(database) as connection, connection:
                connection.execute(
                    """INSERT INTO runs
                       (run_id, version, model_digest, elapsed_ms, decision, reason, category)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, mode, digest, elapsed, result["decision"],
                     result["reason"], result["category"]),
                )
            self.respond(200, result | {"run_id": run_id})

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        print(f"{mode} listening on http://127.0.0.1:{port}", flush=True)
        server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--port", type=int, default=18680)
    parser.add_argument("--mode", choices=("keyword", "v1"), default="v1")
    parser.add_argument("--ollama", default="http://127.0.0.1:18634")
    args = parser.parse_args()
    try:
        serve(args.db, args.port, args.mode, args.ollama)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
