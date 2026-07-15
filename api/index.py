"""픽셀 오피스 챗봇 API (Vercel 서버리스 / 로컬 겸용).

- POST /api/chat   : NDJSON 스트림 — 에이전트 상태 이벤트 후 최종 답변
- GET  /api/agents : 에이전트 명단 + 모드 정보
- 로컬 실행 시(/api 외 경로): public/ 정적 파일 서빙 (Vercel에서는 CDN이 담당)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.append(os.path.dirname(__file__))

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from _agents import AGENTS, provider, run_pipeline

app = FastAPI(title="Pixel Office Chatbot")


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = Field(default_factory=list)


@app.get("/api")
@app.get("/api/index")
async def api_root():
    return {
        "ok": True,
        "hint": "여기는 API 엔드포인트입니다. 챗봇 화면은 사이트 루트(/)로 접속하세요.",
        "endpoints": ["/api/agents", "POST /api/chat"],
    }


@app.get("/api/agents")
async def get_agents():
    p = provider()
    return {
        "agents": AGENTS,
        "demo": p is None,
        "provider": p["name"] if p else None,
        "model": p["model"] if p else None,
    }


@app.post("/api/chat")
async def chat(req: ChatRequest):
    async def stream():
        async for event in run_pipeline(req.history, req.message):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# 로컬 개발용 정적 서빙 (Vercel에서는 public/을 CDN이 직접 서빙)
if not os.environ.get("VERCEL"):
    public = Path(__file__).resolve().parent.parent / "public"
    if public.is_dir():
        app.mount("/", StaticFiles(directory=public, html=True), name="static")
