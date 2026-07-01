"""
pipeline.py
-----------
The one object the backend/UI talks to.

    nl = NL2SQL("schema/schema.json")
    result = nl.ask("show me portfolios managed by John with market value over 1000000")

`ask()` returns a QueryResult: either a ready-to-run parameterised SELECT (+ a
human-readable preview), or a list of clarifications for the UI to resolve with
the user. It never executes anything — running the SQL against Oracle is the
backend's job, kept separate on purpose.
"""

from .schema_index import SchemaIndex
from .matcher import Matcher
from .intent import IntentParser
from .sql_builder import SQLBuilder, QueryResult
from .normalize import capabilities as _norm_caps


class NL2SQL:
    def __init__(self, schema_path: str, enable_semantic: bool = True,
                 row_cap: int = 100):
        self.index = SchemaIndex(schema_path, enable_semantic=enable_semantic)
        self.matcher = Matcher(self.index)
        self.parser = IntentParser(self.matcher)
        self.builder = SQLBuilder(self.matcher, row_cap=row_cap)

    def ask(self, prompt: str) -> QueryResult:
        intent = self.parser.parse(prompt)
        return self.builder.build(intent)

    def capabilities(self) -> dict:
        caps = dict(_norm_caps())
        caps.update(self.index.capabilities())
        return caps
