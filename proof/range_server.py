"""Serve files with range requests and simulated network limits, logging each request.

Usage: python proof/range_server.py ROOT PORT LATENCY_MS MBIT_PER_SECOND

A server on the same machine has no network delay, which would flatter every
measurement. This adds a delay per request and limits throughput, so results stand
in for a static host reached over the internet.
"""

import mimetypes
import re
import sys
import time
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
PORT = int(sys.argv[2])
LATENCY = float(sys.argv[3]) / 1000
BYTES_PER_SECOND = float(sys.argv[4]) * 1e6 / 8

RANGE = re.compile(r"bytes=(\d+)-(\d*)")


class Handler(BaseHTTPRequestHandler):
    # Keep-alive and validators, as a real static host has. Without them, Chromium's
    # handling of range requests is much slower than it would be in real use.
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _serve(self, send_body):
        relative = Path(self.path.split("?")[0].lstrip("/"))
        path = ROOT / relative
        if ".." in relative.parts or not path.is_file():
            self.send_error(404)
            return
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        match = RANGE.fullmatch(self.headers.get("Range", ""))
        if match:
            start = int(match.group(1))
            end = min(int(match.group(2)) if match.group(2) else size - 1, size - 1)
            status = 206
        length = end - start + 1
        print(f"{time.time():.3f}", self.command, relative.name, start, length, flush=True)
        self.send_response(status)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        stat = path.stat()
        self.send_header("ETag", f'"{stat.st_size:x}-{int(stat.st_mtime):x}"')
        self.send_header("Last-Modified", formatdate(stat.st_mtime, usegmt=True))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not send_body:
            return
        time.sleep(LATENCY + length / BYTES_PER_SECOND)
        with open(path, "rb") as handle:
            handle.seek(start)
            self.wfile.write(handle.read(length))

    def do_HEAD(self):
        self._serve(send_body=False)

    def do_GET(self):
        self._serve(send_body=True)


class Server(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


Server(("127.0.0.1", PORT), Handler).serve_forever()
