import sys
import os

# Add parent directory to sys.path so we can import engine
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import NL2SQL
from engine.intent import Condition, ConditionGroup

def run_tests():
    # Initialize engine with the schema
    schema_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema", "schema.json")
    nl = NL2SQL(schema_path, enable_semantic=False)
    
    print("=========================================")
    print("RUNNING NESTED FILTER PARSER TESTS")
    print("=========================================")
    
    # Test case 1: Standard flat AND filter
    prompt1 = "show portfolios with manager named John and base currency is USD"
    print(f"\nPrompt: {prompt1}")
    res1 = nl.ask(prompt1)
    print("Generated SQL:\n", res1.sql)
    assert res1.ok
    assert "WHERE" in res1.sql
    assert "AND" in res1.sql
    assert " OR " not in res1.sql
    
    # Test case 2: Parentheses with AND/OR
    prompt2 = "show portfolios with (base currency is USD and manager named John) or market value greater than 500000"
    print(f"\nPrompt: {prompt2}")
    res2 = nl.ask(prompt2)
    print("Generated SQL:\n", res2.sql)
    assert res2.ok
    # Expect output structure with parentheses
    assert "OR" in res2.sql
    assert "AND" in res2.sql
    
    # Test case 3: Nested parenthesized groups
    prompt3 = "show portfolios with ((base currency is USD or base currency is EUR) and market value greater than 100000) and manager named John"
    print(f"\nPrompt: {prompt3}")
    res3 = nl.ask(prompt3)
    print("Generated SQL:\n", res3.sql)
    assert res3.ok
    assert "OR" in res3.sql
    
    # Test case 4: BETWEEN query nested inside parens
    prompt4 = "show portfolios with (market value between 100000 and 500000) and manager named John"
    print(f"\nPrompt: {prompt4}")
    res4 = nl.ask(prompt4)
    print("Generated SQL:\n", res4.sql)
    assert res4.ok
    assert "BETWEEN" in res4.sql
    
    print("\nALL TEST CASES PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    run_tests()
