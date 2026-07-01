"""
db_config.py
------------
Loads Oracle connection details from config.yaml (or environment variables),
so connection.py and backend.py share one source of truth.

You literally just fill in config.yaml. Env vars (DB_HOST, DB_PORT,
DB_SERVICE_NAME, DB_USER, DB_PASSWORD, DB_SCHEMA) override the file if set — handy
for keeping secrets out of the file.

No hard dependency on PyYAML: if it's installed we use it, otherwise a tiny
flat "key: value" parser handles the simple config below.
"""

import os
from dataclasses import dataclass, field


@dataclass
class DBConfig:
    host: str = ""
    port: int = 1521
    service_name: str = ""
    user: str = ""
    password: str = ""
    schema: str = ""                 # owner to introspect; defaults to user
    tables: list = field(default_factory=list)   # optional whitelist

    @property
    def dsn(self) -> str:
        return f"{self.host}:{self.port}/{self.service_name}"

    @property
    def owner(self) -> str:
        return (self.schema or self.user or "").upper()

    def missing(self) -> list:
        need = ["host", "service_name", "user", "password"]
        return [k for k in need if not getattr(self, k)]


def _parse_simple_yaml(text: str) -> dict:
    """Flat 'key: value' parser (no nesting) as a PyYAML-free fallback.
    Supports a simple inline list:  tables: [A, B, C]"""
    out = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip() or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if val.startswith("[") and val.endswith("]"):
            items = [v.strip().strip("'\"") for v in val[1:-1].split(",")]
            out[key] = [i for i in items if i]
        else:
            out[key] = val.strip().strip("'\"")
    return out


def _load_file(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        text = f.read()
    if path.endswith(".json"):
        import json
        return json.loads(text)
    try:
        import yaml            # optional
        return yaml.safe_load(text) or {}
    except Exception:
        return _parse_simple_yaml(text)


def load_config(path: str = "config.yaml") -> DBConfig:
    data = _load_file(path)
    cfg = DBConfig(
        host=str(data.get("host", "")),
        port=int(data.get("port", 1521) or 1521),
        service_name=str(data.get("service_name", "")),
        user=str(data.get("user", "")),
        password=str(data.get("password", "")),
        schema=str(data.get("schema", "")),
        tables=list(data.get("tables", []) or []),
    )
    # env overrides
    cfg.host = os.getenv("DB_HOST", cfg.host)
    cfg.port = int(os.getenv("DB_PORT", cfg.port) or cfg.port)
    cfg.service_name = os.getenv("DB_SERVICE_NAME", cfg.service_name)
    cfg.user = os.getenv("DB_USER", cfg.user)
    cfg.password = os.getenv("DB_PASSWORD", cfg.password)
    cfg.schema = os.getenv("DB_SCHEMA", cfg.schema)
    return cfg
