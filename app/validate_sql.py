"""Deterministic SQL validation — the only gate between LLM output and Snowflake.

Pure function, no I/O, no LLM. Everything here is unit-testable.
Defense in depth: this gate + a read-only Snowflake role.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

# The LLM's entire world. Anything else is rejected.
ALLOWED_VIEWS = {
    "STATE_DEMOGRAPHICS", "STATE_ECONOMY", "STATE_EDUCATION", "STATE_HOUSING",
    "COUNTY_DEMOGRAPHICS", "COUNTY_ECONOMY", "COUNTY_EDUCATION", "COUNTY_HOUSING",
}
MAX_LIMIT = 50


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    sql: str = ""       # rewritten SQL (LIMIT enforced) when ok
    error: str = ""     # machine-readable reason when not ok — fed back to the
                        # generator on retry, so keep it descriptive


def validate_sql(raw_sql: str) -> ValidationResult:
    """Accept a single read-only SELECT over allowlisted views; reject all else."""
    if not raw_sql or not raw_sql.strip():
        return ValidationResult(False, error="empty SQL")

    # 1. Must parse as exactly one statement (kills 'SELECT 1; DROP ...').
    try:
        statements = sqlglot.parse(raw_sql, read="snowflake")
    except sqlglot.errors.ParseError as e:
        return ValidationResult(False, error=f"SQL does not parse: {e}")
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return ValidationResult(False, error=f"expected 1 statement, got {len(statements)}")
    tree = statements[0]

    # 2. Root must be a SELECT (CTEs/WITH resolve to Select in sqlglot).
    if not isinstance(tree, exp.Select):
        return ValidationResult(
            False, error=f"only SELECT is allowed, got {type(tree).__name__}"
        )

    # 3. No write/DDL/exec nodes anywhere in the tree — belt over braces,
    #    catches constructs smuggled into subqueries.
    forbidden = (
        exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop,
        exp.Alter, exp.TruncateTable, exp.Grant, exp.Command, exp.Use,
    )
    for node in tree.walk():
        if isinstance(node, forbidden):
            return ValidationResult(
                False, error=f"forbidden construct: {type(node).__name__}"
            )

    # 4. Every table reference must be an allowlisted view. CTE names defined
    #    in the query itself are legitimate references, not tables.
    cte_names = {cte.alias_or_name.upper() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name.upper()          # bare name, ignoring db/schema parts
        if name in cte_names:
            continue
        if name not in ALLOWED_VIEWS:
            return ValidationResult(
                False,
                error=(
                    f"table '{name}' is not an allowed view; "
                    f"use only: {', '.join(sorted(ALLOWED_VIEWS))}"
                ),
            )

    # 5. Enforce LIMIT <= MAX_LIMIT on the outer query (inject if absent).
    limit_node = tree.args.get("limit")
    if limit_node is None:
        tree = tree.limit(MAX_LIMIT)
    else:
        try:
            current = int(limit_node.expression.this)
        except (TypeError, ValueError, AttributeError):
            return ValidationResult(False, error="non-numeric LIMIT")
        if current > MAX_LIMIT:
            tree = tree.limit(MAX_LIMIT)

    return ValidationResult(True, sql=tree.sql(dialect="snowflake"))
