"""Local flow viewer: python -m memorydelta.server  ->  http://localhost:8765
Serves ui/index.html, the latest results/comparison.json, and a button to rerun the live demo."""
import json
import pathlib
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results" / "comparison.json"
BENCH = ROOT / "results" / "bench.json"
UI = ROOT / "ui" / "index.html"
state = {"running": False, "started_at": None, "finished_at": None, "exit_code": None}
lock = threading.Lock()


def _run_demo():
    log = (ROOT / "results" / "demo_live.txt").open("w")
    proc = subprocess.run([sys.executable, "-m", "memorydelta.demo"], cwd=ROOT, stdout=log,
                          stderr=subprocess.STDOUT, env={**__import__("os").environ, "COLUMNS": "160"})
    with lock:
        state.update(running=False, finished_at=time.time(), exit_code=proc.returncode)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, UI.read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/results":
            if not RESULTS.exists():
                return self._send(404, {"error": "no results yet; run the demo"})
            return self._send(200, RESULTS.read_bytes())
        if self.path == "/api/bench":
            if not BENCH.exists():
                return self._send(404, {"error": "no benchmark yet; run python -m memorydelta.bench"})
            return self._send(200, BENCH.read_bytes())
        if self.path == "/api/status":
            with lock:
                return self._send(200, state)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/run":
            return self._send(404, {"error": "not found"})
        with lock:
            if state["running"]:
                return self._send(409, state)
            state.update(running=True, started_at=time.time(), finished_at=None, exit_code=None)
        threading.Thread(target=_run_demo, daemon=True).start()
        self._send(202, state)

    def log_message(self, *a):
        pass


def main(port=8765):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"MemoryDelta flow viewer on http://localhost:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
