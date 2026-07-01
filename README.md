# NL2SQL (local, no-LLM) — prototype

Ask an Oracle database questions in plain English and get back the SQL — **without
sending any data, schema, or keys to an external LLM.** Built for an ops team on a
fintech (SimCorp) database where the data is too sensitive to leave the box.

This repo currently contains the **hard part, proven end to end**: the
`natural language → intent → SQL` engine, running against a mock schema. No
database or API needed to try it.

```bash
python demo.py                       # run the built-in prompt suite
python demo.py "top 5 portfolios by market value"
```

## Why it works without an LLM

Real column names are cryptic (`MKT_VAL`, `CCY`, `SEC_CCY`). The trick: we never
try to understand the *name* — we match the user's words against the human-written
**column comments** (the SimCorp glossary). Those comments are the semantic layer.

Two things translate a sentence:

| Concern | Handled by | How |
|---|---|---|
| **Structure** (select/where/group/order/agg/operators) | `lexicon.py` + `intent.py` | A fixed, generous English→SQL grammar. "greater than"→`>`, "how many"→`COUNT`, "sorted by"→`ORDER BY`. Deterministic. |
| **Meaning** (which column is "currency"?) | `matcher.py` + `schema_index.py` | Score each column by IDF-weighted keyword overlap + fuzzy + (optional) semantic embedding, then rank. |

## Ranking — where the project lives or dies

For a phrase like *"currency"*, the score blends:

- **keyword** — IDF-weighted recall (did we hit the column's *meaningful* words? common words like "currency" count less) blended with **precision** (how much of *this* column is actually about the term — beats a column that merely mentions it in passing).
- **fuzzy** — typo tolerance (`rapidfuzz`, falls back to stdlib).
- **semantic** — sentence-transformer cosine, so *"who runs it"* → *"manager"* (optional, local, offline).
- **focus-table bias** — once we know the query's subject table, its columns are preferred, so *"currency"* means *this* table's currency, not another's.

When the top two columns are too close to call, the engine **doesn't guess** — it
returns a clarification for the UI to ask the user (**human in the loop**).

## Everything is optional / degrades gracefully

`python demo.py` runs on a bare interpreter using pure-Python fallbacks. Install
`requirements.txt` to turn on spaCy lemmatization, rapidfuzz, and — most
importantly — **local semantic matching** (`all-MiniLM-L6-v2`, ~90 MB, downloaded
once, runs offline, data never leaves the machine). `nl.capabilities()` shows
what's active.

## Safety posture (it's a fintech DB)

- The builder **only emits `SELECT`** — there is no code path to DML/DDL.
- Values are **bound parameters** (`:b0`…), never string-concatenated → injection-safe. A human-readable `preview` with values inlined is produced separately, for display only.
- Every query is capped with `FETCH FIRST N ROWS ONLY`.
- Run the real thing under a **read-only Oracle user**.

## Architecture (the two-phase plan)

```
BUILD TIME   connection.py  →  reads config (host/port/service/creds),
                               introspects Oracle, writes schema/schema.json
                               (tables, columns, types, comments, FKs)

RUN TIME     backend.py     →  loads schema.json into an in-memory INVERTED INDEX
                               + embeddings at startup; never re-reads Oracle
                               metadata. Only the final generated SELECT hits the DB.
             frontend       →  chat UI, shows SQL + rows + clarification prompts
```

**Meaning drift / human in the loop:** when `RECQ` stops meaning "record number"
and starts meaning "record quotient", a human either fixes the DB comment and
re-runs `connection.py`, or edits `schema.json` directly. The engine only ever
trusts `schema.json`, so both paths work.

## Files

```
engine/
  lexicon.py       English→SQL grammar (triggers, comparators, aggregations…)
  normalize.py     tokenize / lemmatize / spell-correct  (optional spaCy, rapidfuzz)
  schema_index.py  load schema.json → inverted index + IDF + embeddings
  matcher.py       phrase → ranked columns, with ambiguity detection  ← THE HARD PART
  intent.py        sentence → structured Intent (select/where/group/order/limit)
  sql_builder.py   Intent → safe parameterised SELECT, FK join inference
  pipeline.py      NL2SQL facade:  nl.ask("...") → QueryResult
schema/schema.json mock SimCorp-style schema (stands in for connection.py output)
demo.py            end-to-end demonstration
```

## Not yet built (deliberately scoped out of this prototype)

`connection.py` (Oracle introspection), `backend.py` (FastAPI), the frontend,
cross-schema search (multiple `schema.json`), and conversation cache (follow-up
questions on a result set). The engine is designed so these bolt on around it.
