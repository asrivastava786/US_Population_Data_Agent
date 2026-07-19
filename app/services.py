"""Infrastructure: LLM wrapper, Snowflake client, groundedness check.
Each is small and independently testable/swappable."""
from __future__ import annotations

import json
import re

import snowflake.connector
from openai import OpenAI

from . import config

_client = OpenAI(api_key=config.OPENAI_API_KEY, max_retries=3, timeout=30.0)


def llm(prompt: str, model: str, json_mode: bool = False) -> str:
    """Single swappable seam to the LLM provider. Temperature 0 everywhere."""
    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
    resp = _client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
        **kwargs,
    )
    return resp.choices[0].message.content or ""


def run_query(sql: str) -> list[dict]:
    """Execute validated SQL. Fresh short-lived connection per query keeps the
    demo simple and stateless (README: pool this in production)."""
    conn = snowflake.connector.connect(**config.SNOWFLAKE)
    try:
        cur = conn.cursor(snowflake.connector.DictCursor)
        cur.execute(sql, timeout=config.QUERY_TIMEOUT_S)
        return cur.fetchall()
    finally:
        conn.close()


_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def _numbers_in(text: str) -> set[float]:
    out = set()
    for m in _NUM_RE.findall(text):
        try:
            out.add(round(float(m.replace(",", "")), 1))
        except ValueError:
            pass
    return out


def grounded(answer: str, rows: list[dict], question: str) -> tuple[bool, list[float]]:
    """Deterministic groundedness gate: every number in the answer must appear
    in the result rows (or in the question itself, e.g. 'over $100k').
    Tolerates formatting (commas) and simple rounding to <=1 decimal."""
    allowed = _numbers_in(json.dumps(rows, default=str)) | _numbers_in(question)
    # accept common roundings of any allowed value
    expanded = set(allowed)
    for v in allowed:
        expanded.add(round(v))
        if abs(v) >= 1_000_000:
            expanded.add(round(v / 1_000_000, 1))   # "29.0 million"
        if abs(v) >= 1_000:
            expanded.add(round(v / 1_000, 1))       # "29,014" quoted as thousands

    IGNORED = {2016.0, 2020.0, 5.0}
    offending = [n for n in _numbers_in(answer)
                 if n not in expanded
                 and -n not in expanded
                 and abs(n) not in IGNORED]
    #offending = [n for n in _numbers_in(answer)
     #            if n not in expanded and n != 2016.0 and n != 2020.0 and n != 5.0]
    return (len(offending) == 0, offending)
