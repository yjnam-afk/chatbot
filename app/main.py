"""픽셀 오피스 챗봇 서버.

- POST /api/chat   : 멀티 에이전트 파이프라인으로 응답 생성
- GET  /api/events : 에이전트 상태 SSE 스트림 (픽셀 오피스 시각화용)
- GET  /api/agents : 에이전트 명단 + 모드 정보
- GET  /           : 정적 프론트엔드 (static/)
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agents import AGENTS, MODEL, llm_available, run_pipeline

app = FastAPI(title="Pixel Office Chatbot")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class EventHub:
    """단순 인메모리 pub/sub — 접속한 모든 브라우저에 에이전트 상태를 방송한다."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def publish(self, event: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                self._subscribers.discard(q)


hub = EventHub()
sessions: dict[str, list[dict]] = {}


async def emit(agent_id: str, state: str, activity: str = "") -> None:
    await hub.publish(
        {"type": "agent", "agent": agent_id, "state": state, "activity": activity, "ts": time.time()}
    )


class ChatRequest(BaseModel):
    session_id: str = "default"
    message: str


@app.get("/api/agents")
async def get_agents():
    return {"agents": AGENTS, "demo": not llm_available(), "model": MODEL}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    history = sessions.setdefault(req.session_id, [])
    result = await run_pipeline(history, req.message, emit)
    history.append({"role": "user", "content": req.message})
    history.append({"role": "assistant", "content": result["reply"]})
    if len(history) > 40:
        del history[: len(history) - 40]
    return result


@app.get("/api/events")
async def events():
    async def stream():
        q = hub.subscribe()
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            hub.unsubscribe(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
