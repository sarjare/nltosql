"""
intent.py
---------
Parses the STRUCTURE of the request out of the sentence: projection columns,
aggregation, where-conditions, group by, having, order by, limit.

This is a deterministic heuristic parser (no LLM). It works by recognising the
lexicon phrases from lexicon.py as boundary markers and slicing the sentence
into regions:

    [trigger] [projection/aggregation] [FILTER] [GROUP] [ORDER] [LIMIT]

Column *phrases* found here are handed to matcher.py to resolve to real columns.
It won't parse every possible English sentence — but it covers the prompt
families the ops team asked for (show / where / group by / having / order by /
aggregation / dates / limit / simple joins), and every unresolved bit is
surfaced rather than silently guessed.
"""

import re
from dataclasses import dataclass, field

from . import lexicon
from .matcher import Matcher


class ConditionNode:
    def walk(self) -> list['Condition']:
        raise NotImplementedError


@dataclass
class Condition(ConditionNode):
    column_phrase: str
    op: str
    value: str
    connector: str = "AND"     # how it joins to the PREVIOUS condition
    resolved: object = None    # MatchResult, filled by resolver

    def walk(self) -> list['Condition']:
        return [self]


@dataclass
class ConditionGroup(ConditionNode):
    connector: str  # "AND" | "OR"
    items: list[ConditionNode]

    def walk(self) -> list['Condition']:
        res = []
        for item in self.items:
            res.extend(item.walk())
        return res


@dataclass
class OrderSpec:
    column_phrase: str
    direction: str = "ASC"
    resolved: object = None


@dataclass
class Intent:
    raw: str
    normalized: str
    projection_phrases: list[str] = field(default_factory=list)
    aggregation: str | None = None          # COUNT/SUM/AVG/MAX/MIN
    aggregation_phrase: str | None = None    # what to aggregate
    conditions: ConditionNode | None = None
    group_phrases: list[str] = field(default_factory=list)
    having: tuple | None = None              # (op, number) on the aggregate
    order: OrderSpec | None = None
    limit: int | None = None
    notes: list[str] = field(default_factory=list)


# --- small helpers ---------------------------------------------------------

def _find_first_phrase(text: str, phrases) -> tuple[int, str]:
    """Earliest position at which any phrase occurs (word-boundary), longest at
    that position wins. Returns (pos, phrase) or (-1, '')."""
    best_pos, best_phrase = len(text) + 1, ""
    for ph in phrases:
        m = re.search(r"\b" + re.escape(ph) + r"\b", text)
        if m:
            if m.start() < best_pos or (m.start() == best_pos and len(ph) > len(best_phrase)):
                best_pos, best_phrase = m.start(), ph
    return (best_pos, best_phrase) if best_phrase else (-1, "")


def _strip_leading(text: str, phrases) -> str:
    for ph in sorted(phrases, key=len, reverse=True):
        if text.startswith(ph + " "):
            return text[len(ph) + 1:].strip()
        if text == ph:
            return ""
    return text


def _split_projection(text: str) -> list[str]:
    parts = re.split(r"\s*,\s*|\s+and\s+", text)
    return [p.strip() for p in parts if p.strip()]


# --- the parser ------------------------------------------------------------

class IntentParser:
    def __init__(self, matcher: Matcher):
        self.matcher = matcher

    def parse(self, prompt: str) -> Intent:
        raw = prompt
        text = re.sub(r"\s+", " ", prompt.lower()).strip()
        intent = Intent(raw=raw, normalized=text)

        text = _strip_leading(text, lexicon.SELECT_TRIGGERS)

        text = self._extract_limit(text, intent)
        text = self._extract_order(text, intent)
        text = self._extract_topn_order(text, intent)
        text = self._extract_group(text, intent)
        text, filter_text = self._split_filter_region(text)
        self._extract_aggregation(text, intent)      # reads projection region
        self._extract_conditions(filter_text, intent)

        self._resolve(intent)
        return intent

    # -- limit -------------------------------------------------------------
    def _extract_limit(self, text, intent):
        m = re.search(r"\b(top|first|bottom|limit)\s+(\d+)\b", text)
        if m:
            intent.limit = int(m.group(2))
            if m.group(1) in ("top", "bottom"):
                # top N implies an order; default desc for top, asc for bottom
                intent._top_dir = "DESC" if m.group(1) == "top" else "ASC"
            text = (text[:m.start()] + " " + text[m.end():]).strip()
        return text

    # -- order by ----------------------------------------------------------
    def _extract_order(self, text, intent):
        pos, ph = _find_first_phrase(text, lexicon.ORDER_TRIGGERS.keys())
        if pos < 0:
            return text
        after = text[pos + len(ph):].strip()
        direction = "ASC"
        for w in lexicon.ORDER_DESC_WORDS:
            if re.search(r"\b" + re.escape(w) + r"\b", after):
                direction = "DESC"
                after = re.sub(r"\b" + re.escape(w) + r"\b", "", after).strip()
        for w in lexicon.ORDER_ASC_WORDS:
            if re.search(r"\b" + re.escape(w) + r"\b", after):
                direction = "ASC"
                after = re.sub(r"\b" + re.escape(w) + r"\b", "", after).strip()
        intent.order = OrderSpec(column_phrase=after, direction=direction)
        return text[:pos].strip()

    # -- top-N ordering ("top 5 portfolios by market value") ---------------
    def _extract_topn_order(self, text, intent):
        if not getattr(intent, "_top_dir", None) or intent.order is not None:
            return text
        pos, ph = _find_first_phrase(text, ["by"])
        if pos < 0:
            return text
        after = text[pos + len(ph):].strip()
        intent.order = OrderSpec(column_phrase=after, direction=intent._top_dir)
        return text[:pos].strip()

    # -- group by ----------------------------------------------------------
    def _extract_group(self, text, intent):
        # Prefer explicit multi-word triggers; only honour bare "by" when it is
        # clearly a grouping (an aggregation word appears somewhere in the text).
        triggers = [t for t in lexicon.GROUP_TRIGGERS if t != "by"]
        pos, ph = _find_first_phrase(text, triggers)
        if pos < 0:
            has_agg = any(re.search(r"\b" + re.escape(a) + r"\b", text)
                          for a in lexicon.AGGREGATIONS)
            if has_agg:
                pos, ph = _find_first_phrase(text, ["by"])
            if pos < 0:
                return text
        after = text[pos + len(ph):].strip()
        intent.group_phrases = _split_projection(after)
        return text[:pos].strip()

    # -- filter region -----------------------------------------------------
    def _split_filter_region(self, text):
        """Return (projection_text, filter_text). The filter starts at the first
        WHERE-introducer, or at the first comparator phrase if none present."""
        introducers = ["with", "where", "whose", "having", "for", "that have", "that has"]
        pos, ph = _find_first_phrase(text, introducers)
        # also consider an early comparator (e.g. "value greater than 1000000")
        cpos, cph = _find_first_phrase(text, lexicon.COMPARATORS.keys())
        if cpos >= 0 and (pos < 0 or cpos < pos):
            # back up to the start of the column phrase for this comparator:
            # everything from the previous projection boundary. Simplest robust
            # choice: treat the whole head as projection up to the token right
            # before the comparator's column word. We keep the column word with
            # the filter by starting the filter at the last comma/'and' before.
            head = text[:cpos]
            cut = max(head.rfind(","), head.rfind(" and "))
            start = cut + 1 if cut >= 0 else 0
            return text[:start].strip(), text[start:].strip()
        if pos < 0:
            return text.strip(), ""
        return text[:pos].strip(), text[pos:].strip()

    # -- aggregation + projection -----------------------------------------
    def _extract_aggregation(self, proj_text, intent):
        pos, ph = _find_first_phrase(proj_text, lexicon.AGGREGATIONS.keys())
        if pos >= 0:
            intent.aggregation = lexicon.AGGREGATIONS[ph]
            target = proj_text[pos + len(ph):].strip()
            # trim trailing filler
            target = _strip_leading(target, ["of", "the"])
            intent.aggregation_phrase = target or None
            # anything before the aggregation word may still be projection
            head = proj_text[:pos].strip()
            if head:
                intent.projection_phrases = _split_projection(head)
        else:
            proj_text = _strip_leading(proj_text, ["me", "all", "the"])
            if proj_text:
                intent.projection_phrases = _split_projection(proj_text)

    # -- conditions --------------------------------------------------------
    def _extract_conditions(self, filter_text, intent):
        if not filter_text:
            return
        filter_text = _strip_leading(
            filter_text, ["with", "where", "whose", "having", "for", "that have", "that has"])

        # Tokenize preserving parenthesis boundaries
        tokens = self._tokenize_filter(filter_text)
        if not tokens:
            return

        try:
            node, idx = self._parse_expression(tokens, 0)
            intent.conditions = node
        except Exception as e:
            intent.notes.append(f"Failed to parse filter expression: {str(e)}")

    def _tokenize_filter(self, text: str) -> list[str]:
        # Pad parentheses with spaces, then split
        padded = text.replace("(", " ( ").replace(")", " ) ")
        return padded.split()

    def _parse_expression(self, tokens: list[str], start: int) -> tuple[ConditionNode, int]:
        # expr -> term ( 'or' term )*
        node, idx = self._parse_term(tokens, start)
        while idx < len(tokens) and tokens[idx] == "or":
            right_node, idx = self._parse_term(tokens, idx + 1)
            if isinstance(node, ConditionGroup) and node.connector == "OR":
                node.items.append(right_node)
            else:
                node = ConditionGroup(connector="OR", items=[node, right_node])
        return node, idx

    def _parse_term(self, tokens: list[str], start: int) -> tuple[ConditionNode, int]:
        # term -> factor ( 'and' factor )*
        node, idx = self._parse_factor(tokens, start)
        while idx < len(tokens) and tokens[idx] == "and":
            right_node, idx = self._parse_factor(tokens, idx + 1)
            if isinstance(node, ConditionGroup) and node.connector == "AND":
                node.items.append(right_node)
            else:
                node = ConditionGroup(connector="AND", items=[node, right_node])
        return node, idx

    def _parse_factor(self, tokens: list[str], start: int) -> tuple[ConditionNode, int]:
        if start >= len(tokens):
            raise ValueError("Unexpected end of filter expression")
        if tokens[start] == "(":
            node, idx = self._parse_expression(tokens, start + 1)
            if idx >= len(tokens) or tokens[idx] != ")":
                raise ValueError("Missing closing parenthesis")
            return node, idx + 1
        else:
            return self._parse_clause_tokens(tokens, start)

    def _parse_clause_tokens(self, tokens: list[str], start: int) -> tuple[ConditionNode, int]:
        idx = start
        has_between = False
        between_and_consumed = False
        clause_tokens = []
        
        while idx < len(tokens):
            t = tokens[idx]
            if t == "or" or t == ")":
                break
            if t == "between":
                has_between = True
            if t == "and":
                if has_between and not between_and_consumed:
                    between_and_consumed = True
                else:
                    break
            clause_tokens.append(t)
            idx += 1
            
        clause_str = " ".join(clause_tokens)
        cond = self._parse_clause(clause_str)
        if cond is None:
            # Fallback/Unresolved clause wrapper
            cond = Condition(column_phrase=clause_str, op="=", value="")
        return cond, idx

    def _parse_clause(self, clause):
        if not clause:
            return None
            
        # Support BETWEEN clause
        between_match = re.search(r"(.+?)\bbetween\b\s+(.+?)\s+and\s+(.+)", clause)
        if between_match:
            col = between_match.group(1).strip()
            val = f"{between_match.group(2).strip()}|{between_match.group(3).strip()}"
            return Condition(column_phrase=col, op="BETWEEN", value=val)

        # find an explicit comparator phrase
        pos, ph = _find_first_phrase(clause, lexicon.COMPARATORS.keys())
        if pos > 0:                     # need a non-empty column phrase on the left
            col = clause[:pos].strip()
            value = clause[pos + len(ph):].strip()
            op = lexicon.COMPARATORS[ph]
            # "portfolios named X" / "manager called Y": the target is the NAME
            # column of that noun, so steer the matcher toward it.
            if ph in ("named", "called") and "name" not in col:
                col = f"{col} name".strip()
            return Condition(column_phrase=col, op=op, value=value)

        # no comparator: assume equality. Greedily consume the longest leading
        # column phrase that resolves well; the remainder is the value.
        tokens = clause.split()
        for take in range(len(tokens) - 1, 0, -1):
            col_phrase = " ".join(tokens[:take])
            value = " ".join(tokens[take:])
            res = self.matcher.match(col_phrase)
            if res.best and res.best.score >= 0.45 and value:
                return Condition(column_phrase=col_phrase, op="=", value=value)
                
        # If still unmatched, return None so fallback can wrap it
        return None

    # -- resolve phrases to columns ---------------------------------------
    def _resolve(self, intent):
        for cp in [*intent.projection_phrases]:
            pass  # resolved lazily in sql_builder via matcher; kept here for clarity
        if intent.conditions:
            for c in intent.conditions.walk():
                c.resolved = self.matcher.match(c.column_phrase)
        if intent.order:
            intent.order.resolved = self.matcher.match(intent.order.column_phrase)
        # apply top-N implied direction if order had no explicit column
        if getattr(intent, "_top_dir", None) and intent.order is None and intent.aggregation:
            intent.order = OrderSpec(column_phrase=intent.aggregation_phrase or "",
                                     direction=intent._top_dir)
            intent.order.resolved = self.matcher.match(intent.order.column_phrase)
