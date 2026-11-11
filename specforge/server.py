from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict

from .orchestrator import SpecForgeOrchestrator


ORCHESTRATOR = SpecForgeOrchestrator()
STATIC_STUDIO_PATH = Path(__file__).resolve().parent / "studio.html"


class SpecForgeRequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: Any) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/missions":
            self._send_json(200, {"missions": ORCHESTRATOR.list_missions()})
            return
        if self.path == "/api/health":
            self._send_json(200, {"status": "ok", "engine": "SpecForge Python Subprocess Sandbox"})
            return
        if self.path in ("/", "/index.html"):
            if STATIC_STUDIO_PATH.exists():
                html_bytes = STATIC_STUDIO_PATH.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_bytes)))
                self.end_headers()
                self.wfile.write(html_bytes)
                return
        self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body_bytes = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data: Dict[str, Any] = json.loads(body_bytes.decode("utf-8"))
        except Exception:
            data = {}

        if self.path == "/api/run":
            mission_id = str(data.get("mission_id", "token_bucket_limiter"))
            custom_code = data.get("custom_initial_code")
            auto_heal = bool(data.get("auto_heal", True))
            max_iterations = int(data.get("max_iterations", 4))
            run = ORCHESTRATOR.run_pipeline(
                mission_id=mission_id,
                max_iterations=max_iterations,
                custom_initial_code=custom_code,
                auto_heal=auto_heal,
            )
            self._send_json(200, run.to_dict())
            return

        if self.path == "/api/sandbox":
            mission_id = str(data.get("mission_id", "token_bucket_limiter"))
            code = str(data.get("code", ""))
            spec = ORCHESTRATOR.architect.create_spec(mission_id)
            sb_res = ORCHESTRATOR.sandbox.execute(
                iteration=1,
                code=code,
                function_name=spec.function_name,
                edge_cases=spec.edge_cases,
            )
            diag = ORCHESTRATOR.reviewer.diagnose(1, spec, code, sb_res)
            self._send_json(
                200,
                {
                    "sandbox_result": sb_res.to_dict(),
                    "diagnosis": diag.to_dict(),
                },
            )
            return

        if self.path == "/api/mutate":
            mission_id = str(data.get("mission_id", "token_bucket_limiter"))
            code = data.get("code")
            auto_harden = bool(data.get("auto_harden_qa", True))
            mut_report = ORCHESTRATOR.run_mutation_only(
                mission_id=mission_id,
                code=str(code) if code else None,
                auto_harden_qa=auto_harden,
            )
            self._send_json(200, mut_report.to_dict())
            return

        self._send_json(404, {"error": "Unknown endpoint"})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = HTTPServer((host, port), SpecForgeRequestHandler)
    print(f"SpecForge Python API Server listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
