"""The agent pipeline: a fixed LangGraph workflow, not an autonomous agent.
route -> generate_sql -> validate -> execute -> answer, with bounded retries.
The LLM is called at exactly three points, as a pure text function."""
from __future__ import annotations

import json
from typing import Literal, TypedDict

from langgraph.graph import END, StateGraph

from . import config
from .prompts import ANSWER_PROMPT, ROUTER_PROMPT, SCHEMA_BLOCK, SCOPE, SQL_PROMPT
from .services import grounded, llm, run_query
from .validate_sql import validate_sql


class AgentState(TypedDict, total=False):
    history: list[dict]            # [{"role": "user"|"assistant", "content": str}]
    message: str                   # latest user message
    standalone_question: str
    route: Literal["answer", "clarify", "decline_scope", "decline_offtopic"]
    reason: str
    sql: str
    sql_attempts: int
    sql_feedback: str              # validator/Snowflake error fed to regeneration
    rows: list[dict]
    execution_failed: bool
    answer: str


def route_node(state: AgentState) -> AgentState:
    history = "\n".join(
        f"{m['role']}: {m['content']}" for m in state.get("history", [])[-config.HISTORY_WINDOW:]
    ) or "(none)"
    raw = llm(
        ROUTER_PROMPT.format(scope=SCOPE, history=history, message=state["message"]),
        model=config.ROUTER_MODEL,
        json_mode=True,
    )
    try:
        parsed = json.loads(raw)
        route = parsed.get("route", "clarify")
        if route not in ("answer", "clarify", "decline_scope", "decline_offtopic"):
            route = "clarify"
        return {
            "standalone_question": parsed.get("standalone_question", state["message"]),
            "route": route,
            "reason": parsed.get("reason", ""),
        }
    except (json.JSONDecodeError, AttributeError):
        # fail safe, never fail open
        return {"standalone_question": state["message"], "route": "clarify",
                "reason": "I wasn't sure how to interpret that — could you rephrase?"}


def generate_sql_node(state: AgentState) -> AgentState:
    feedback = ""
    if state.get("sql_feedback"):
        feedback = (f"\nYour previous attempt failed with this error — fix it:\n"
                    f"{state['sql_feedback']}\nPrevious SQL:\n{state.get('sql','')}")
    sql = llm(
        SQL_PROMPT.format(schema=SCHEMA_BLOCK,
                          question=state["standalone_question"],
                          feedback=feedback),
        model=config.MAIN_MODEL,
    ).strip().removeprefix("```sql").removeprefix("```").removesuffix("```").strip()
    return {"sql": sql, "sql_attempts": state.get("sql_attempts", 0) + 1}


def validate_node(state: AgentState) -> AgentState:
    result = validate_sql(state["sql"])
    if result.ok:
        return {"sql": result.sql, "sql_feedback": ""}
    return {"sql_feedback": f"validation: {result.error}"}


def execute_node(state: AgentState) -> AgentState:
    try:
        rows = run_query(state["sql"])
        return {"rows": rows, "sql_feedback": "", "execution_failed": False}
    except Exception as e:  # snowflake errors carry useful messages
        return {"sql_feedback": f"execution: {e}", "execution_failed": True}


def answer_node(state: AgentState) -> AgentState:
    route = state.get("route", "answer")
    if route == "decline_offtopic":
        return {"answer": "I can only help with questions about US population and "
                          "demographics from the Census dataset. " + state.get("reason", "")}
    if route == "decline_scope":
        return {"answer": (state.get("reason") or "That's outside what this demo covers.")
                + " I can help with population, income, employment, poverty, education "
                  "and housing at national, state or county level."}
    if route == "clarify":
        return {"answer": state.get("reason") or "Could you clarify what you mean?"}
    if state.get("sql_feedback"):  # exhausted retries
        return {"answer": "I couldn't construct a reliable query for that question. "
                          "Try rephrasing — for example, name a specific state or "
                          "county and one measure (population, income, education...)."}

    question, rows = state["standalone_question"], state.get("rows", [])
    answer = llm(ANSWER_PROMPT.format(question=question,
                                      rows=json.dumps(rows, default=str)),
                 model=config.MAIN_MODEL)
    ok, offending = grounded(answer, rows, question)
    if not ok:  # one deterministic re-ask, then honest failure
        answer = llm(ANSWER_PROMPT.format(question=question,
                                          rows=json.dumps(rows, default=str))
                     + f"\nIMPORTANT: your draft contained numbers not present in "
                       f"the rows ({offending}). Use ONLY values from the rows.",
                     model=config.MAIN_MODEL)
        ok, _ = grounded(answer, rows, question)
        if not ok:
            return {"answer": "I retrieved data for this but couldn't produce a "
                              "reliably grounded summary. Raw result: "
                              + json.dumps(rows[:5], default=str)}
    return {"answer": answer}


def _after_route(state: AgentState) -> str:
    return "generate_sql" if state["route"] == "answer" else "answer"


def _after_validate(state: AgentState) -> str:
    if not state.get("sql_feedback"):
        return "execute"
    return "generate_sql" if state["sql_attempts"] < config.MAX_SQL_ATTEMPTS else "answer"


def _after_execute(state: AgentState) -> str:
    if not state.get("execution_failed"):
        return "answer"
    return "generate_sql" if state["sql_attempts"] < config.MAX_SQL_ATTEMPTS else "answer"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("route", route_node)
    g.add_node("generate_sql", generate_sql_node)
    g.add_node("validate", validate_node)
    g.add_node("execute", execute_node)
    g.add_node("answer", answer_node)
    g.set_entry_point("route")
    g.add_conditional_edges("route", _after_route)
    g.add_edge("generate_sql", "validate")
    g.add_conditional_edges("validate", _after_validate)
    g.add_conditional_edges("execute", _after_execute)
    g.add_edge("answer", END)
    return g.compile()
