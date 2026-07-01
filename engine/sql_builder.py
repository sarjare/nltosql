"""
sql_builder.py
--------------
Takes a parsed Intent, resolves every phrase to a real column (via matcher),
works out the FROM table(s) and any FK join, and emits SQL DETERMINISTICALLY.

Safety posture (this is a fintech DB):
  * We only ever build SELECT — there is no code path that emits DML/DDL.
  * Values are bound as parameters (:b0, :b1 ...), never string-concatenated,
    so the generated query is injection-safe. A human-readable preview with the
    values inlined is produced separately, for display only.
  * Every query is capped with FETCH FIRST N ROWS ONLY.

When a phrase can't be resolved, or two columns tie, we DON'T guess — we return
a clarification so the UI can ask the human (the "human in the loop" step).
"""

import re
from dataclasses import dataclass, field

from .intent import Intent
from .matcher import Matcher
from . import lexicon

DEFAULT_ROW_CAP = 100


@dataclass
class Clarification:
    phrase: str
    reason: str                 # "ambiguous" | "unresolved"
    options: list = field(default_factory=list)   # list of (key, comment, score)


@dataclass
class QueryResult:
    ok: bool
    sql: str = ""               # parameterised
    preview: str = ""           # values inlined, display only
    params: dict = field(default_factory=dict)
    tables: list = field(default_factory=list)
    clarifications: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    intent: Intent = None


class SQLBuilder:
    def __init__(self, matcher: Matcher, row_cap: int = DEFAULT_ROW_CAP):
        self.matcher = matcher
        self.index = matcher.index
        self.row_cap = row_cap

    # -- table resolution --------------------------------------------------
    def _match_table(self, phrase: str):
        """Best-matching table for a bare-noun phrase like 'portfolios'."""
        from .normalize import tokenize, lemmatize_tokens, fuzzy_ratio
        p_toks = set(lemmatize_tokens(tokenize(phrase)))
        best, best_score = None, 0.0
        for table, tmeta in self.index.tables.items():
            t_toks = set(lemmatize_tokens(tokenize(table + " " + tmeta.get("comment", ""))))
            overlap = len(p_toks & t_toks) / max(len(p_toks), 1)
            fz = max((fuzzy_ratio(pt, tt) for pt in p_toks for tt in t_toks), default=0.0)
            score = 0.7 * overlap + 0.3 * fz
            if score > best_score:
                best, best_score = table, score
        return best, best_score

    def _is_table_noun(self, phrase: str) -> bool:
        """True when the phrase names a table more than any single column."""
        if not phrase:
            return False
        t, tscore = self._match_table(phrase)
        col = self.matcher.match(phrase).best
        colscore = col.score if col else 0.0
        return bool(t and tscore >= 0.5 and tscore >= colscore)

    def _determine_focus(self, intent) -> str | None:
        """The 'subject' table of the request, used to bias column resolution
        so 'currency' means the currency column of THIS table, not another's."""
        subject_phrases = list(intent.projection_phrases)
        if intent.aggregation_phrase:
            subject_phrases.append(intent.aggregation_phrase)
        for ph in subject_phrases:
            t, tscore = self._match_table(ph)
            col = self.matcher.match(ph).best
            if t and tscore >= 0.5 and tscore >= (col.score if col else 0.0):
                return t
        for ph in intent.projection_phrases:
            col = self.matcher.match(ph).best
            if col:
                return col.table
        if intent.aggregation_phrase:
            col = self.matcher.match(intent.aggregation_phrase).best
            if col:
                return col.table
        # last resort: the subject noun sitting inside a condition phrase, e.g.
        # "portfolios named X" -> focus PORTFOLIOS_POC.
        for c in intent.conditions:
            t, tscore = self._match_table(c.column_phrase)
            if t and tscore >= 0.5:
                return t
        return None

    # -- main --------------------------------------------------------------
    def build(self, intent: Intent) -> QueryResult:
        res = QueryResult(ok=True, intent=intent)
        select_items: list[str] = []
        used_columns: list[tuple[str, str]] = []   # (table, column, score)
        forced_table = None

        focus = self._determine_focus(intent)
        measure_key = None   # column being aggregated; must not also be GROUP BY

        # ---- projection / aggregation ----
        if intent.aggregation:
            # "count portfolios" / "how many portfolios" -> COUNT(*)
            if intent.aggregation == "COUNT" and (
                    not intent.aggregation_phrase
                    or self._is_table_noun(intent.aggregation_phrase)):
                select_items.append("COUNT(*)")
                if intent.aggregation_phrase:
                    t, _ = self._match_table(intent.aggregation_phrase)
                    forced_table = forced_table or t
            else:
                col = self._resolve_column(intent.aggregation_phrase, res, focus_table=focus)
                if col:
                    used_columns.append(col)
                    measure_key = f"{col[0]}.{col[1]}"
                    select_items.append(f"{intent.aggregation}({self._qual(col)})")
                else:
                    select_items.append(f"{intent.aggregation}(*)")
            # group columns become leading projected columns (never the measure)
            for gp in intent.group_phrases:
                col = self._resolve_column(gp, res, focus_table=focus,
                                           exclude_keys={measure_key} if measure_key else None)
                if col:
                    used_columns.append(col)
                    select_items.insert(0, self._qual(col))
        else:
            if not intent.projection_phrases:
                res.warnings.append("No columns detected; defaulting to SELECT *.")
            for ph in intent.projection_phrases:
                # is this phrase really a table name (=> SELECT *)?
                if self._is_table_noun(ph):
                    t, _ = self._match_table(ph)
                    forced_table = forced_table or t
                    continue
                col = self._resolve_column(ph, res, focus_table=focus)
                if col:
                    used_columns.append(col)
                    select_items.append(self._qual(col))

        # ---- conditions ----
        where_parts = []
        params = {}
        pidx = 0
        for cond in intent.conditions:
            col = self._resolve_column(cond.column_phrase, res, focus_table=focus)
            if not col:
                continue
            used_columns.append(col)
            frag, params, pidx = self._render_condition(col, cond, params, pidx, res)
            if frag:
                prefix = "" if not where_parts else f" {cond.connector} "
                where_parts.append(prefix + frag)

        # ---- group / having / order ----
        group_cols = []
        for gp in intent.group_phrases:
            col = self._resolve_column(gp, res, focus_table=focus,
                                       exclude_keys={measure_key} if measure_key else None)
            if col:
                group_cols.append(col)
                used_columns.append(col)

        order_sql = ""
        if intent.order and intent.order.column_phrase:
            col = self._resolve_column(intent.order.column_phrase, res, focus_table=focus)
            if col:
                used_columns.append(col)
                order_sql = f"{self._qual(col)} {intent.order.direction}"

        # ---- decide FROM + joins ----
        tables = []
        for tc in used_columns:
            t = tc[0]
            if t not in tables:
                tables.append(t)
        if forced_table and forced_table not in tables:
            tables.insert(0, forced_table)
        if not tables:
            res.ok = False
            res.warnings.append("Could not determine which table to query.")
            return res
        res.tables = tables

        from_sql, join_warn = self._build_from(tables)
        if join_warn:
            res.warnings.append(join_warn)

        # ---- assemble ----
        if not select_items:
            select_items = ["*"]
        qualified = len(tables) > 1

        sql = f"SELECT {', '.join(select_items)}\nFROM {from_sql}"
        preview = sql
        if where_parts:
            where_clause = "".join(where_parts)
            sql += f"\nWHERE {where_clause}"
            preview += f"\nWHERE {self._inline(where_clause, params)}"
        if group_cols:
            g = ", ".join(self._qual(c) for c in group_cols)
            sql += f"\nGROUP BY {g}"
            preview += f"\nGROUP BY {g}"
        if intent.having and intent.aggregation:
            hop, hval = intent.having
            agg_expr = select_items[-1]
            sql += f"\nHAVING {agg_expr} {hop} {hval}"
            preview += f"\nHAVING {agg_expr} {hop} {hval}"
        if order_sql:
            sql += f"\nORDER BY {order_sql}"
            preview += f"\nORDER BY {order_sql}"

        cap = intent.limit or self.row_cap
        sql += f"\nFETCH FIRST {cap} ROWS ONLY"
        preview += f"\nFETCH FIRST {cap} ROWS ONLY"

        res.sql = sql if not qualified else sql  # qualification already applied
        res.preview = preview
        res.params = params
        return res

    # -- column resolution -------------------------------------------------
    def _resolve_column(self, phrase, res, add_clarification=True,
                        focus_table=None, exclude_keys=None):
        if not phrase:
            return None
        match = self.matcher.match(phrase, focus_table=focus_table,
                                   exclude_keys=exclude_keys)
        return self._resolve_from_result(match, phrase, res, add_clarification)

    def _resolve_from_result(self, match, phrase, res, add_clarification=True):
        if match is None or match.is_empty:
            if add_clarification:
                res.clarifications.append(Clarification(phrase, "unresolved"))
            return None
        if match.is_ambiguous and add_clarification:
            res.clarifications.append(Clarification(
                phrase, "ambiguous",
                options=[(c.key, c.comment, c.score) for c in match.candidates[:3]]))
        best = match.best
        return (best.table, best.column, best.score)

    # -- SQL fragments -----------------------------------------------------
    def _qual(self, col):
        return f"{col[0]}.{col[1]}"

    def _build_from(self, tables):
        if len(tables) == 1:
            return tables[0], None
        base = tables[0]
        clause = base
        joined = {base}
        warn = None
        for t in tables[1:]:
            fk = None
            for j in list(joined):
                fk = self.index.find_fk(j, t)
                if fk:
                    break
            if fk:
                clause += (f"\n  JOIN {t} ON "
                           f"{fk['from_table']}.{fk['from_column']} = "
                           f"{fk['to_table']}.{fk['to_column']}")
                joined.add(t)
            else:
                clause += f"\n  CROSS JOIN {t}"
                warn = (f"No foreign key found linking {t} to "
                        f"{', '.join(joined)}; used CROSS JOIN — please verify.")
                joined.add(t)
        return clause, warn

    def _render_condition(self, col, cond, params, pidx, res=None):
        colref = self._qual(col)
        op = cond.op
        is_text = self._is_text_column(col)

        if op == "BETWEEN":
            lo, hi = (cond.value.split("|") + [""])[:2]
            k1, k2 = f"b{pidx}", f"b{pidx+1}"
            params[k1] = self._typed(lo)
            params[k2] = self._typed(hi)
            return f"{colref} BETWEEN :{k1} AND :{k2}", params, pidx + 2

        if op in ("LIKE", "LIKE_PREFIX", "LIKE_SUFFIX"):
            v = cond.value.strip().strip("'\"")
            if op == "LIKE":
                v = f"%{v}%"
            elif op == "LIKE_PREFIX":
                v = f"{v}%"
            else:
                v = f"%{v}"
            k = f"b{pidx}"
            params[k] = v
            return f"UPPER({colref}) LIKE UPPER(:{k})", params, pidx + 1

        value = cond.value
        typed = self._typed(value)

        # If the column is a DATE but the value came through with stray words
        # ("date 2024-12-31"), pull the date out of it and drop the rest.
        if self._is_date_column(col) and not (isinstance(typed, dict) and "__date__" in typed):
            found, leftover = self._find_date(value)
            if found:
                typed = {"__date__": found}
                if leftover and res is not None:
                    res.warnings.append(f"Ignored unparsed filter text: '{leftover}'.")

        # For ordinal comparators the value should be a single number or date.
        # If it didn't parse as a date and isn't already numeric, keep only the
        # leading number and flag the leftover rather than folding words in.
        is_date_val = isinstance(typed, dict) and "__date__" in typed
        if op in (">", "<", ">=", "<=") and not is_date_val and isinstance(typed, str):
            m = re.match(r"\s*(-?\d+(?:\.\d+)?)\b(.*)", value)
            if m:
                value = m.group(1)
                typed = self._typed(value)
                rest = m.group(2).strip()
                if rest and res is not None:
                    res.warnings.append(f"Ignored unparsed filter text: '{rest}'.")

        k = f"b{pidx}"
        params[k] = typed
        # case-insensitive comparison only for TEXT columns (never on DATE/NUMBER)
        if op in ("=", "!=") and is_text and isinstance(typed, str):
            return f"UPPER({colref}) {op} UPPER(:{k})", params, pidx + 1
        return f"{colref} {op} :{k}", params, pidx + 1

    def _is_text_column(self, col) -> bool:
        doc = self.index.by_key.get(f"{col[0]}.{col[1]}")
        t = (doc.type if doc else "").upper()
        return t.startswith(("VARCHAR", "NVARCHAR", "CHAR", "NCHAR", "CLOB"))

    def _is_date_column(self, col) -> bool:
        doc = self.index.by_key.get(f"{col[0]}.{col[1]}")
        t = (doc.type if doc else "").upper()
        return t.startswith(("DATE", "TIMESTAMP"))

    def _find_date(self, value: str):
        """Find a date anywhere in the string; return (normalized_or_None, leftover)."""
        v = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", value.lower())
        patterns = [
            r"\d{4}-\d{1,2}-\d{1,2}",
            r"\d{1,2}[/-]\d{1,2}[/-]\d{4}",
            r"\d{1,2}\s+[a-z]+\s+\d{4}",
            r"[a-z]+\s+\d{1,2}\s+\d{4}",
            r"[a-z]+\s+\d{4}",
        ]
        for pat in patterns:
            m = re.search(pat, v)
            if m:
                parsed = self._parse_date(m.group(0))
                if parsed:
                    leftover = (v[:m.start()] + v[m.end():]).strip()
                    return parsed, leftover
        return None, value

    # -- value typing ------------------------------------------------------
    def _typed(self, value: str):
        v = value.strip().strip("'\"")
        if re.fullmatch(r"-?\d+", v):
            return int(v)
        if re.fullmatch(r"-?\d+\.\d+", v):
            return float(v)
        d = self._parse_date(v)
        if d:
            return {"__date__": d}      # marker; rendered via TO_DATE in preview
        return v

    def _parse_date(self, v: str):
        v = v.strip().lower()
        # drop ordinal suffixes: "1st july 2026" -> "1 july 2026"
        v = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", v)
        v = re.sub(r"\bof\b", " ", v)
        v = re.sub(r"\s+", " ", v).strip()

        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", v)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", v)
        if m:
            return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
        # "1 july 2026" (DD Month YYYY)
        m = re.fullmatch(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})", v)
        if m and m.group(2) in lexicon.MONTHS:
            return f"{int(m.group(3)):04d}-{lexicon.MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"
        # "july 1 2026" (Month DD YYYY)
        m = re.fullmatch(r"([a-z]+)\s+(\d{1,2})\s+(\d{4})", v)
        if m and m.group(1) in lexicon.MONTHS:
            return f"{int(m.group(3)):04d}-{lexicon.MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"
        # "july 2026" (Month YYYY) -> first of month
        m = re.fullmatch(r"([a-z]+)\s+(\d{4})", v)
        if m and m.group(1) in lexicon.MONTHS:
            return f"{int(m.group(2)):04d}-{lexicon.MONTHS[m.group(1)]:02d}-01"
        return None

    def _inline(self, clause, params):
        """Render a display-only version with bind values substituted."""
        out = clause
        for k, v in params.items():
            if isinstance(v, dict) and "__date__" in v:
                lit = f"TO_DATE('{v['__date__']}','YYYY-MM-DD')"
            elif isinstance(v, (int, float)):
                lit = str(v)
            else:
                lit = "'" + str(v).replace("'", "''") + "'"
            out = out.replace(f":{k}", lit)
        return out
