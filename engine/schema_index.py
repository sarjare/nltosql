"""
schema_index.py
---------------
Loads schema.json ONCE at startup and builds the in-memory structures the
matcher needs:

  1. A flat list of "column documents" — each column plus its comment words,
     table context, type, and FK info.
  2. An INVERTED INDEX  word -> {column keys that mention it}.  This is the
     answer to "looping over 300 columns is slow": lookup is O(matching words),
     not O(columns).
  3. (optional) SENTENCE-TRANSFORMER EMBEDDINGS of each column's comment, so the
     matcher can score by *meaning* and not just word overlap. Loaded locally,
     offline, no API — fits the "sensitive data never leaves the box" rule.

If sentence-transformers isn't installed, embeddings are skipped and matching
degrades gracefully to keyword + fuzzy scoring.
"""

import json
import math
from collections import defaultdict

from .normalize import tokenize, lemmatize_tokens
from .lexicon import STOPWORDS

# ---- optional sentence-transformers ---------------------------------------
_ST_MODEL = None
_ST = False
try:
    from sentence_transformers import SentenceTransformer, util as _st_util
    _ST = True
except Exception:
    _ST = False


class ColumnDoc:
    """One searchable column."""
    __slots__ = ("table", "column", "type", "comment", "is_pk", "key", "text",
                 "comment_tokens", "name_tokens", "tokens", "embedding")

    def __init__(self, table, column, meta):
        self.table = table
        self.column = column
        self.type = meta.get("type", "")
        self.comment = meta.get("comment", "")
        self.is_pk = meta.get("pk", False)
        self.key = f"{table}.{column}"
        # The "document" the user's words are matched against: the human comment
        # plus the (often cryptic) column name split on underscores.
        name_words = column.lower().replace("_", " ")
        self.text = f"{self.comment} {name_words}".strip()
        # Comment tokens carry the meaning; name tokens (mkt, val, ccy) are a
        # secondary signal. Keeping them separate lets "precision" (how much of
        # the column is ABOUT the term) ignore cryptic-name noise.
        self.comment_tokens = {t for t in lemmatize_tokens(tokenize(self.comment))
                               if t not in STOPWORDS}
        self.name_tokens = set(lemmatize_tokens(tokenize(name_words)))
        self.tokens = self.comment_tokens | self.name_tokens
        self.embedding = None  # filled in later if ST available


class SchemaIndex:
    def __init__(self, path: str, enable_semantic: bool = True):
        with open(path, "r") as f:
            self.raw = json.load(f)
        self.schema_name = self.raw.get("schema_name", "")
        self.tables = self.raw["tables"]

        self.columns: list[ColumnDoc] = []
        self.by_key: dict[str, ColumnDoc] = {}
        self.inverted: dict[str, set[str]] = defaultdict(set)
        self.vocabulary: set[str] = set()
        self.foreign_keys: list[dict] = []
        self.idf: dict[str, float] = {}
        self._mean_idf: float = 1.0

        self._build_docs()
        self._build_inverted()
        self._build_idf()
        self._collect_fks()

        self.semantic = False
        if enable_semantic and _ST:
            self._build_embeddings()

    # -- build -------------------------------------------------------------
    def _build_docs(self):
        for table, tmeta in self.tables.items():
            for col, cmeta in tmeta["columns"].items():
                doc = ColumnDoc(table, col, cmeta)
                self.columns.append(doc)
                self.by_key[doc.key] = doc

    def _build_inverted(self):
        for doc in self.columns:
            for tok in doc.tokens:
                self.inverted[tok].add(doc.key)
                self.vocabulary.add(tok)
        # also index table names / comments so "portfolios" finds the table
        for table, tmeta in self.tables.items():
            for tok in lemmatize_tokens(tokenize(table + " " + tmeta.get("comment", ""))):
                self.vocabulary.add(tok)

    def _build_idf(self):
        """Rarer terms are more discriminative. 'currency' appears in many
        columns -> low idf; 'inception' in one -> high idf."""
        n = max(len(self.columns), 1)
        df = defaultdict(int)
        for doc in self.columns:
            for tok in doc.tokens:
                df[tok] += 1
        for tok, d in df.items():
            self.idf[tok] = math.log((1 + n) / (1 + d)) + 1.0
        self._mean_idf = (sum(self.idf.values()) / len(self.idf)) if self.idf else 1.0

    def token_idf(self, tok: str) -> float:
        return self.idf.get(tok, self._mean_idf)

    def _collect_fks(self):
        for table, tmeta in self.tables.items():
            for fk in tmeta.get("foreign_keys", []):
                self.foreign_keys.append({
                    "from_table": table,
                    "from_column": fk["column"],
                    "to_table": fk["ref_table"],
                    "to_column": fk["ref_column"],
                })

    def _build_embeddings(self):
        global _ST_MODEL
        try:
            if _ST_MODEL is None:
                # small, fast, fully local model
                _ST_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
            texts = [d.text for d in self.columns]
            embs = _ST_MODEL.encode(texts, convert_to_tensor=True, normalize_embeddings=True)
            for doc, e in zip(self.columns, embs):
                doc.embedding = e
            self.semantic = True
        except Exception:
            self.semantic = False

    # -- lookup helpers ----------------------------------------------------
    def candidates_for_tokens(self, tokens: list[str]) -> set[str]:
        """Column keys touched by any of these tokens (via inverted index)."""
        hits: set[str] = set()
        for t in tokens:
            hits |= self.inverted.get(t, set())
        return hits

    def embed(self, text: str):
        if not self.semantic or _ST_MODEL is None:
            return None
        return _ST_MODEL.encode(text, convert_to_tensor=True, normalize_embeddings=True)

    def cosine(self, emb_a, emb_b) -> float:
        if emb_a is None or emb_b is None:
            return 0.0
        return float(_st_util.cos_sim(emb_a, emb_b)[0][0])

    def find_fk(self, table_a: str, table_b: str):
        for fk in self.foreign_keys:
            if {fk["from_table"], fk["to_table"]} == {table_a, table_b}:
                return fk
        return None

    def capabilities(self) -> dict:
        return {"semantic_embeddings": self.semantic,
                "num_columns": len(self.columns),
                "num_index_terms": len(self.inverted)}
