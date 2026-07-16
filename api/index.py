"""기술사 답안 사무소 챗봇 API (Vercel 서버리스 / 로컬 겸용).

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

app = FastAPI(title="기술사 답안 사무소")


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = Field(default_factory=list)
    kind: str | None = None  # 프런트 교시형 칩 수동 선택("1교시형"|"2교시형") — 규칙 판별 오버라이드


@app.get("/api/debug")
async def debug():
    """배포 진단용 — 함수 번들에 어떤 파일이 들어있는지 확인."""
    static_dir = Path(__file__).resolve().parent / "_static"
    return {
        "api_dir_files": sorted(p.name for p in Path(__file__).resolve().parent.iterdir()),
        "static_exists": static_dir.is_dir(),
        "static_files": sorted(p.name for p in static_dir.iterdir()) if static_dir.is_dir() else [],
    }


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
        async for event in run_pipeline(req.history, req.message, kind_hint=req.kind):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# 정적 서빙 — 모든 요청이 이 앱으로 오므로 FastAPI가 직접 서빙한다.
# 정적 파일은 api/_static/에 둔다: Vercel 파이썬 빌더가 루트의 public/은
# 번들에서 제외하지만 api/ 디렉터리는 통째로 포함하기 때문.
_static = Path(__file__).resolve().parent / "_static"
if _static.is_dir():
    app.mount("/", StaticFiles(directory=_static, html=True), name="static")
