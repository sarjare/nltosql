"""
demo.py — prove the NL->SQL engine end to end against the mock schema.

Run:
    python demo.py                 # runs the built-in prompt suite
    python demo.py "your question" # try one of your own

No database or API needed. If spaCy / sentence-transformers / rapidfuzz are
installed the matching gets sharper, but it runs on a bare interpreter too.
"""

import sys
from engine import NL2SQL

PROMPTS = [
    "show me portfolios",
    "list portfolio name and market value",
    "show portfolios with market value greater than 1000000",
    "get portfolios whose currency is USD",
    "count portfolios",
    "how many portfolios per currency",
    "average market value by currency",
    "total market value grouped by base currency sorted by total descending",
    "top 5 portfolios by market value",
    "show securities with asset class equity",
    "portfolios with manager name John",             # cross-table (needs join)
    "find holdings with quantity greater than 500 as of date 2024-12-31",
    "show portfolios named Global Equity Fund",
    "list securities where name contains bond",
]


def show(nl, prompt):
    print("\n" + "=" * 78)
    print("Q:", prompt)
    print("-" * 78)
    r = nl.ask(prompt)
    if r.clarifications:
        print("NEEDS CLARIFICATION:")
        for c in r.clarifications:
            print(f"  • '{c.phrase}' — {c.reason}")
            for opt in c.options:
                print(f"       ? {opt[0]}  ({opt[1]})  score={opt[2]}")
    if r.warnings:
        for w in r.warnings:
            print("  ! ", w)
    if r.ok and r.preview:
        print("SQL:")
        for line in r.preview.splitlines():
            print("   ", line)
        if r.tables:
            print("   tables:", ", ".join(r.tables))
    elif not r.ok:
        print("  (could not build a query)")


def main():
    nl = NL2SQL("schema/schema.json")
    print("Engine capabilities:", nl.capabilities())
    if len(sys.argv) > 1:
        show(nl, " ".join(sys.argv[1:]))
    else:
        for p in PROMPTS:
            show(nl, p)


if __name__ == "__main__":
    main()
