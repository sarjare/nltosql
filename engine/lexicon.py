"""
lexicon.py
----------
The deterministic "grammar" of the NL->SQL translation.

These maps are what let us AVOID an LLM: instead of asking a model what the
user meant, we recognise a fixed (but generous) vocabulary of English phrasings
and map each to a piece of SQL. Column *meaning* is handled separately by the
semantic matcher (matcher.py); this file only handles the STRUCTURE of the query
(select / where / group / order / aggregation / operators).

Everything here is lowercase; the normalizer lowercases input before matching.
Multi-word phrases are matched longest-first so "greater than or equal to" wins
over "greater than".
"""

# Words that mean "I want to SELECT / retrieve rows". Strip these from the prompt.
SELECT_TRIGGERS = [
    "show me", "show", "list", "display", "get me", "get", "give me", "give",
    "find", "fetch", "retrieve", "i want", "i need", "pull", "extract",
    "select", "return", "what are", "what is", "who are", "how many",
]

# Aggregation function detection.  phrase -> SQL function.
# "how many" / "number of" / "count of" -> COUNT
AGGREGATIONS = {
    "count":        "COUNT",
    "number of":    "COUNT",
    "count of":     "COUNT",
    "how many":     "COUNT",
    "total number": "COUNT",
    "sum":          "SUM",
    "sum of":       "SUM",
    "total":        "SUM",
    "average":      "AVG",
    "avg":          "AVG",
    "mean":         "AVG",
    "maximum":      "MAX",
    "max":          "MAX",
    "highest":      "MAX",
    "largest":      "MAX",
    "biggest":      "MAX",
    "minimum":      "MIN",
    "min":          "MIN",
    "lowest":       "MIN",
    "smallest":     "MIN",
}

# Comparison operator phrases -> SQL operator. Longest phrase should be tried first.
COMPARATORS = {
    "greater than or equal to": ">=",
    "greater than or equal":    ">=",
    "at least":                 ">=",
    "no less than":             ">=",
    "less than or equal to":    "<=",
    "less than or equal":       "<=",
    "at most":                  "<=",
    "no more than":             "<=",
    "greater than":             ">",
    "more than":                ">",
    "larger than":              ">",
    "bigger than":              ">",
    "higher than":              ">",
    "above":                    ">",
    "over":                     ">",
    "exceeds":                  ">",
    "less than":                "<",
    "lower than":               "<",
    "smaller than":             "<",
    "below":                    "<",
    "under":                    "<",
    # date-oriented comparators
    "on or after":              ">=",
    "on or before":             "<=",
    "after":                    ">",
    "since":                    ">=",
    "later than":               ">",
    "before":                   "<",
    "earlier than":             "<",
    "prior to":                 "<",
    "not equal to":             "!=",
    "not equals":               "!=",
    "is not":                   "!=",
    "equal to":                 "=",
    "equals":                   "=",
    "is":                       "=",
    "of":                       "=",
    "named":                    "=",
    "called":                   "=",
    "with":                     "=",
    "where":                    "=",
    "whose":                    "=",
    "contains":                 "LIKE",
    "containing":               "LIKE",
    "like":                     "LIKE",
    "starts with":              "LIKE_PREFIX",
    "starting with":            "LIKE_PREFIX",
    "ends with":                "LIKE_SUFFIX",
    "between":                  "BETWEEN",
}

# Phrases introducing an ORDER BY, and the implied direction.
ORDER_TRIGGERS = {
    "sorted by":     None,
    "sort by":       None,
    "ordered by":    None,
    "order by":      None,
    "in order of":   None,
    "arranged by":   None,
}
ORDER_DESC_WORDS = ["descending", "desc", "highest first", "largest first", "high to low", "top"]
ORDER_ASC_WORDS  = ["ascending", "asc", "lowest first", "smallest first", "low to high"]

# Phrases introducing a GROUP BY (only meaningful when an aggregation is present).
GROUP_TRIGGERS = ["grouped by", "group by", "for each", "per", "by"]

# Phrases introducing HAVING (aggregate filter after a group by).
HAVING_TRIGGERS = ["having", "with more than", "with at least", "with fewer than"]

# Limit / top-N.
LIMIT_TRIGGERS = ["top", "first", "limit", "bottom"]

# Logical joiners between where-conditions.
LOGICAL = {"and": "AND", "or": "OR"}

# Words that carry no matching signal; dropped before column matching.
# NOTE: operator/keyword words are intentionally NOT here — they are consumed
# by the structural parser before column matching runs.
STOPWORDS = {
    "a", "an", "the", "of", "to", "in", "on", "for", "from", "that", "this",
    "there", "their", "please", "me", "all", "any", "some", "which", "whose",
    "and", "or", "with", "where", "are", "is", "was", "were", "do", "does",
}

# Month names -> number, for date parsing.
MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
