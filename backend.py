"""
backend.py
----------
RUN-TIME server. Loads schema.json into the NL2SQL engine once at startup and
serves a tiny JSON API. Pure Python standard library — nothing to pip install to
try the natural-language -> SQL conversion.

    python backend.py                 # http://127.0.0.1:8000
    python backend.py --port 9000 --schema schema/schema.json

Endpoints
    GET  /health                      liveness + engine capabilities
    GET  /capabilities                which matchers are active (spacy/semantic…)
    GET  /schema                      the loaded tables/columns
    POST /ask    {"prompt": "..."}    -> generated SQL + clarifications (NO DB needed)
    POST /run    {"prompt": "..."}    -> /ask, then execute against Oracle, return rows

Try it:
    curl -s localhost:8000/ask -H 'content-type: application/json' \
         -d '{"prompt":"show portfolios with market value greater than 1000000"}' | python -m json.tool
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from engine import NL2SQL

ENGINE: NL2SQL = None       # set in main()
SCHEMA_PATH = "schema/schema.json"


# ---- serialization --------------------------------------------------------

def result_to_dict(r) -> dict:
    return {
        "ok": r.ok,
        "sql": r.sql,                 # parameterised (bind :b0 …)
        "preview": r.preview,         # display-only, values inlined
        "params": r.params,
        "tables": r.tables,
        "needs_clarification": bool(r.clarifications),
        "clarifications": [
            {"phrase": c.phrase, "reason": c.reason,
             "options": [{"column": o[0], "comment": o[1], "score": o[2]}
                         for o in c.options]}
            for c in r.clarifications
        ],
        "warnings": r.warnings,
    }


def _exec_params(params: dict) -> dict:
    """Convert the engine's typed values into DB bind values (date markers ->
    datetime.date)."""
    import datetime
    out = {}
    for k, v in params.items():
        if isinstance(v, dict) and "__date__" in v:
            out[k] = datetime.date.fromisoformat(v["__date__"])
        else:
            out[k] = v
    return out


# ---- request handling -----------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "NL2SQL/0.1"

    def _send(self, code, payload):
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return {}

    def log_message(self, fmt, *args):
        pass   # quiet

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        if self.path.rstrip("/") in ("", "/"):
            return self._send(200, {"service": "NL2SQL",
                                    "endpoints": ["/health", "/capabilities",
                                                  "/schema", "POST /ask", "POST /run"]})
        if self.path == "/health":
            return self._send(200, {"status": "ok",
                                    "capabilities": ENGINE.capabilities()})
        if self.path == "/capabilities":
            return self._send(200, ENGINE.capabilities())
        if self.path == "/schema":
            tables = {t: {"comment": m.get("comment", ""),
                          "columns": {c: cm.get("comment", "")
                                      for c, cm in m["columns"].items()}}
                      for t, m in ENGINE.index.tables.items()}
            return self._send(200, {"schema": ENGINE.index.schema_name, "tables": tables})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/ask":
            prompt = (self._body().get("prompt") or "").strip()
            if not prompt:
                return self._send(400, {"error": "missing 'prompt'"})
            return self._send(200, result_to_dict(ENGINE.ask(prompt)))

        if self.path == "/run":
            prompt = (self._body().get("prompt") or "").strip()
            if not prompt:
                return self._send(400, {"error": "missing 'prompt'"})
            result = ENGINE.ask(prompt)
            payload = result_to_dict(result)
            if not result.ok:
                return self._send(200, payload)
            if result.clarifications:
                payload["executed"] = False
                payload["note"] = "Not executed — resolve clarifications first."
                return self._send(200, payload)
            try:
                rows, columns = self._execute(result)
                payload.update({"executed": True, "columns": columns,
                                "rows": rows, "row_count": len(rows)})
            except ImportError:
                payload.update({"executed": False,
                                "error": "python-oracledb not installed (pip install oracledb)"})
            except Exception as e:
                payload.update({"executed": False, "error": str(e)})
            return self._send(200, payload)

        return self._send(404, {"error": "not found"})

    def _execute(self, result):
        import oracledb
        from db_config import load_config
        cfg = load_config(SCHEMA_CONFIG)
        conn = oracledb.connect(user=cfg.user, password=cfg.password, dsn=cfg.dsn)
        try:
            cur = conn.cursor()
            cur.execute(result.sql, _exec_params(result.params))
            columns = [d[0] for d in cur.description]
            rows = [list(row) for row in cur.fetchall()]
            return rows, columns
        finally:
            conn.close()


SCHEMA_CONFIG = "config.yaml"


def main():
    global ENGINE, SCHEMA_PATH, SCHEMA_CONFIG
    ap = argparse.ArgumentParser(description="NL2SQL backend server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--schema", default="schema/schema.json")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--no-semantic", action="store_true",
                    help="disable sentence-transformer matching")
    args = ap.parse_args()

    SCHEMA_PATH = args.schema
    SCHEMA_CONFIG = args.config
    print(f"Loading schema from {SCHEMA_PATH} ...")
    ENGINE = NL2SQL(SCHEMA_PATH, enable_semantic=not args.no_semantic)
    print("Engine capabilities:", ENGINE.capabilities())

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"NL2SQL backend listening on http://{args.host}:{args.port}")
    print("Try:  curl -s localhost:%d/ask -H 'content-type: application/json' "
          "-d '{\"prompt\":\"show portfolios\"}'" % args.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down.")
        srv.shutdown()


if __name__ == "__main__":
    main()
