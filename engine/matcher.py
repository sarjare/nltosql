"""
matcher.py
----------
THE HARD PART. Given a phrase from the user ("manager name", "market value",
"portfolios"), decide which real column it means — and say so with a score, so
we can (a) rank, and (b) detect when two columns are too close to call and hand
the choice back to the human.

Score for a (phrase, column) pair blends three signals:

    keyword  : token overlap between phrase and the column's comment/name,
               found instantly via the inverted index.
    fuzzy    : best fuzzy-string ratio (handles typos / near-spellings).
    semantic : sentence-transformer cosine between phrase and the column comment
               (handles paraphrase: "who runs it" -> "manager"). 0 if ST absent.

The weights below are the tuning surface of the whole project — this is where
"the project lives or dies", as noted in the design chat.
"""

from dataclasses import dataclass, field

from .normalize import tokenize, lemmatize_tokens, fuzzy_ratio
from .schema_index import SchemaIndex

# scoring weights (sum need not be 1; final score is normalised by comparison)
W_KEYWORD = 0.55
W_FUZZY = 0.20
W_SEMANTIC = 0.60   # semantic gets a strong voice when available

# a match below this is treated as "no match"
MIN_SCORE = 0.30
# if the runner-up is within this fraction of the top, it's ambiguous -> ask human
AMBIGUITY_MARGIN = 0.12


@dataclass
class Candidate:
    key: str
    table: str
    column: str
    comment: str
    score: float
    breakdown: dict = field(default_factory=dict)


@dataclass
class MatchResult:
    phrase: str
    candidates: list[Candidate]

    @property
    def best(self):
        return self.candidates[0] if self.candidates else None

    @property
    def is_empty(self):
        return not self.candidates

    @property
    def is_ambiguous(self):
        if len(self.candidates) < 2:
            return False
        top, second = self.candidates[0].score, self.candidates[1].score
        return top > 0 and (top - second) <= AMBIGUITY_MARGIN * top


class Matcher:
    def __init__(self, index: SchemaIndex):
        self.index = index

    def match(self, phrase: str, top_k: int = 4,
              restrict_tables: set[str] | None = None,
              focus_table: str | None = None,
              exclude_keys: set[str] | None = None) -> MatchResult:
        phrase = phrase.strip()
        if not phrase:
            return MatchResult(phrase, [])

        p_tokens = [t for t in lemmatize_tokens(tokenize(phrase))]
        p_set = set(p_tokens)
        # total idf mass of the query, for weighted recall
        q_idf = sum(self.index.token_idf(t) for t in p_set) or 1.0

        # narrow the search with the inverted index, but keep a fallback to all
        # columns so semantic/fuzzy can still catch paraphrases with no overlap.
        candidate_keys = self.index.candidates_for_tokens(p_tokens)
        search_docs = ([self.index.by_key[k] for k in candidate_keys]
                       if candidate_keys else self.index.columns)

        p_emb = self.index.embed(phrase)

        scored: list[Candidate] = []
        for doc in search_docs:
            if restrict_tables and doc.table not in restrict_tables:
                continue
            if exclude_keys and doc.key in exclude_keys:
                continue

            # keyword score = idf-weighted recall (did we cover the query's
            # meaningful words?) blended with precision (how much of THIS column
            # is actually about those words — beats incidental mentions).
            overlap = p_set & doc.tokens
            recall = sum(self.index.token_idf(t) for t in overlap) / q_idf
            precision = (len(p_set & doc.comment_tokens) /
                         max(len(doc.comment_tokens), 1))
            kw = 0.72 * recall + 0.28 * precision

            # fuzzy: best token-vs-token ratio (typo tolerance)
            fz = 0.0
            for pt in p_tokens:
                for dt in doc.tokens:
                    fz = max(fz, fuzzy_ratio(pt, dt))

            # semantic
            sem = self.index.cosine(p_emb, doc.embedding) if doc.embedding is not None else 0.0

            raw = W_KEYWORD * kw + W_FUZZY * fz + W_SEMANTIC * sem
            # small bump if the phrase word literally appears in the column name
            if any(pt in doc.column.lower() for pt in p_tokens):
                raw += 0.08
            # prefer columns on the query's subject table when one is known
            if focus_table and doc.table == focus_table:
                raw += 0.15

            if raw >= MIN_SCORE:
                scored.append(Candidate(
                    key=doc.key, table=doc.table, column=doc.column,
                    comment=doc.comment, score=round(raw, 4),
                    breakdown={"keyword": round(kw, 3), "fuzzy": round(fz, 3),
                               "semantic": round(sem, 3)},
                ))

        scored.sort(key=lambda c: c.score, reverse=True)
        return MatchResult(phrase, scored[:top_k])
