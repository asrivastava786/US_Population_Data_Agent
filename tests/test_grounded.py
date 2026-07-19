from app.services import grounded

def test_vintage_suffix_not_flagged():
    rows = [{"STATE": "TX", "TOTAL_POPULATION": 28635442.0}]
    ans = ("The total population of Texas is 28,635,442. "
           "(ACS 2016-2020 5-year estimates)")
    ok, offending = grounded(ans, rows, "texas population?")
    assert ok, offending

def test_fabricated_number_still_caught():
    rows = [{"STATE": "TX", "TOTAL_POPULATION": 28635442.0}]
    ok, _ = grounded("Texas has 31,000,000 people. "
                     "(ACS 2016-2020 5-year estimates)", rows, "texas population?")
    assert not ok