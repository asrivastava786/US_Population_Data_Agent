"""FastAPI entrypoint: one /chat endpoint + a static chat page.
Sessions are in-memory (README: fine for a demo; Redis in production)."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .graph import build_graph

app = FastAPI(title="US Census Chat Agent")
_graph = build_graph()
_sessions: dict[str, list[dict]] = {}

STATIC = Path(__file__).resolve().parent.parent / "static"


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    sql: str | None = None   # transparency: show the query that produced the answer


@app.get("/")
def index():
    return FileResponse(STATIC / "chat.html")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    sid = req.session_id or str(uuid.uuid4())
    history = _sessions.setdefault(sid, [])

    result = _graph.invoke({"message": req.message, "history": list(history)})
    answer = result.get("answer", "Something went wrong — please try again.")

    history.append({"role": "user", "content": req.message})
    history.append({"role": "assistant", "content": answer})
    del history[:-20]  # cap memory per session

    shown_sql = result.get("sql") if result.get("route") == "answer" and not result.get("sql_feedback") else None
    return ChatResponse(session_id=sid, answer=answer, sql=shown_sql)
