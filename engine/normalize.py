"""
normalize.py
------------
Turns raw user text into a clean, lower-cased, lemmatized token stream.

Design rule for this prototype: EVERY heavy dependency is optional.
- spaCy         -> better lemmatization/tokenization; falls back to regex + a
                   tiny rule-based lemmatizer if not installed.
- rapidfuzz     -> fast fuzzy spelling correction against the schema vocabulary;
                   falls back to difflib.

This means `python demo.py` runs on a bare interpreter, and simply gets sharper
once you `pip install -r requirements.txt`. The capability in use is printed by
capabilities() so you can see what's active.
"""

import re
from functools import lru_cache

# ---- optional spaCy -------------------------------------------------------
_NLP = None
_SPACY = False
try:
    import spacy
    try:
        _NLP = spacy.load("en_core_web_sm", disable=["ner", "parser"])
        _SPACY = True
    except Exception:
        # model not downloaded
        _NLP = None
        _SPACY = False
except Exception:
    _SPACY = False

# ---- optional rapidfuzz ---------------------------------------------------
try:
    from rapidfuzz import process as _rf_process, fuzz as _rf_fuzz
    _RAPIDFUZZ = True
except Exception:
    import difflib
    _RAPIDFUZZ = False


_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")


def capabilities() -> dict:
    return {
        "spacy": _SPACY,
        "rapidfuzz": _RAPIDFUZZ,
    }


def _simple_lemma(tok: str) -> str:
    """Tiny fallback lemmatizer: enough to fold common plurals/verb forms."""
    for suf in ("ies",):
        if tok.endswith(suf) and len(tok) > 4:
            return tok[: -len(suf)] + "y"
    for suf in ("sses", "shes", "ches", "xes"):
        if tok.endswith(suf) and len(tok) > 4:
            return tok[:-2]
    for suf in ("s",):
        if tok.endswith(suf) and not tok.endswith("ss") and len(tok) > 3:
            return tok[:-1]
    return tok


def tokenize(text: str) -> list[str]:
    """Lower-case tokens, no punctuation."""
    return _TOKEN_RE.findall(text.lower())


def lemmatize_tokens(tokens: list[str]) -> list[str]:
    if _SPACY:
        doc = _NLP(" ".join(tokens))
        return [t.lemma_.lower() if t.lemma_ != "-PRON-" else t.text for t in doc]
    return [_simple_lemma(t) for t in tokens]


def normalize(text: str) -> str:
    """Return a cleaned, lemmatized string (keeps word order and spacing)."""
    toks = lemmatize_tokens(tokenize(text))
    return " ".join(toks)


def spell_correct_tokens(tokens: list[str], vocabulary: set[str],
                         threshold: int = 84) -> list[tuple[str, str]]:
    """
    Correct each token against the schema vocabulary (column/comment words).
    Returns list of (original, corrected). Only changes a token when a close,
    non-identical match exists — so real words are left alone.
    """
    vocab = list(vocabulary)
    out = []
    for tok in tokens:
        if tok in vocabulary or tok.isdigit() or len(tok) <= 2:
            out.append((tok, tok))
            continue
        if _RAPIDFUZZ:
            match = _rf_process.extractOne(tok, vocab, scorer=_rf_fuzz.ratio)
            if match and match[1] >= threshold and match[0] != tok:
                out.append((tok, match[0]))
                continue
        else:
            close = difflib.get_close_matches(tok, vocab, n=1, cutoff=threshold / 100)
            if close and close[0] != tok:
                out.append((tok, close[0]))
                continue
        out.append((tok, tok))
    return out


@lru_cache(maxsize=2048)
def fuzzy_ratio(a: str, b: str) -> float:
    """0..1 similarity between two short strings."""
    if _RAPIDFUZZ:
        return _rf_fuzz.ratio(a, b) / 100.0
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()
