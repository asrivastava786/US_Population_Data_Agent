"""Unit tests for validate_sql — pure function, no Snowflake or LLM needed."""
import pytest

from app.validate_sql import validate_sql, MAX_LIMIT


# ---------- happy path ----------

def test_simple_select_passes_and_gets_limit():
    r = validate_sql("SELECT total_population FROM STATE_DEMOGRAPHICS WHERE state = 'TX'")
    assert r.ok
    assert f"LIMIT {MAX_LIMIT}" in r.sql.upper()

def test_existing_small_limit_is_kept():
    r = validate_sql("SELECT state FROM STATE_ECONOMY ORDER BY median_household_income DESC LIMIT 5")
    assert r.ok and "LIMIT 5" in r.sql.upper()

def test_oversized_limit_is_capped():
    r = validate_sql("SELECT state FROM STATE_ECONOMY LIMIT 5000")
    assert r.ok and f"LIMIT {MAX_LIMIT}" in r.sql.upper()

def test_join_and_qualified_names_pass():
    r = validate_sql("""
        SELECT d.state, d.total_population, e.median_household_income
        FROM CENSUS_AGENT.CURATED.STATE_DEMOGRAPHICS d
        JOIN CENSUS_AGENT.CURATED.STATE_ECONOMY e ON e.state = d.state
        WHERE d.geo_type = 'state'
    """)
    assert r.ok

def test_cte_over_allowed_view_passes():
    r = validate_sql("""
        WITH ranked AS (
            SELECT state, total_population FROM STATE_DEMOGRAPHICS
            WHERE geo_type = 'state'
        )
        SELECT * FROM ranked ORDER BY total_population DESC
    """)
    assert r.ok


# ---------- adversarial ----------

def test_multi_statement_rejected():
    r = validate_sql("SELECT 1; DROP TABLE STATE_ECONOMY")
    assert not r.ok

def test_drop_rejected():
    assert not validate_sql("DROP VIEW CENSUS_AGENT.CURATED.STATE_ECONOMY").ok

def test_insert_rejected():
    assert not validate_sql("INSERT INTO STATE_ECONOMY VALUES (1)").ok

def test_update_rejected():
    assert not validate_sql("UPDATE STATE_ECONOMY SET households = 0").ok

def test_share_table_escape_rejected():
    r = validate_sql(
        'SELECT * FROM US_OPEN_CENSUS_DATA__NEIGHBORHOOD_INSIGHTS__FREE_DATASET'
        '.PUBLIC."2020_CBG_B01"'
    )
    assert not r.ok
    assert "not an allowed view" in r.error

def test_union_smuggling_non_allowed_table_rejected():
    r = validate_sql("""
        SELECT state FROM STATE_ECONOMY
        UNION ALL
        SELECT table_name FROM INFORMATION_SCHEMA.TABLES
    """)
    assert not r.ok

def test_subquery_smuggling_rejected():
    r = validate_sql(
        "SELECT * FROM STATE_ECONOMY WHERE state IN "
        "(SELECT state FROM SOME_OTHER_TABLE)"
    )
    assert not r.ok

def test_scope_manifest_not_queryable_by_llm():
    # The app reads the manifest itself; the LLM's SQL may not.
    assert not validate_sql("SELECT * FROM SCOPE_MANIFEST").ok

def test_gibberish_rejected():
    assert not validate_sql("please give me the population of texas").ok

def test_empty_rejected():
    assert not validate_sql("   ").ok


# ---------- error messages are useful for the retry loop ----------

def test_error_names_allowed_views():
    r = validate_sql("SELECT * FROM POPULATION")
    assert not r.ok and "STATE_DEMOGRAPHICS" in r.error
