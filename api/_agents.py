"""멀티 에이전트 챗봇 파이프라인 (Vercel 서버리스 + 무료 LLM 대응).

harness-100의 38-chatbot-builder 패턴: 오케스트레이터 → NLU → 대화설계 → 응답생성 → 품질검수.

OpenAI 호환 chat/completions API를 사용하므로 무료 LLM을 그대로 쓸 수 있다.
키 환경변수만 설정하면 공급자를 자동 인식한다:

  GROQ_API_KEY    → Groq (llama-3.3-70b-versatile)
  GEMINI_API_KEY  → Google Gemini (gemini-2.5-flash)
  LLM_API_KEY + LLM_BASE_URL + LLM_MODEL → 임의의 OpenAI 호환 엔드포인트

아무 키도 없으면 데모 모드로 동작한다 (LLM 호출 없이 파이프라인 재현).

`run_pipeline`은 async generator다 — 에이전트 상태 이벤트를 순서대로 yield하고
마지막에 {"type": "reply", ...}를 yield한다. 서버리스에서는 요청 간 메모리가
공유되지 않으므로, 상태 이벤트를 별도 채널(SSE 허브) 대신 응답 스트림에 실어 보낸다.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, AsyncIterator

import httpx

AGENTS = [
    {"id": "orchestrator", "name": "코디", "role": "오케스트레이터", "color": "#f2b544"},
    {"id": "nlu", "name": "누리", "role": "NLU 분석가", "color": "#5bc8f5"},
    {"id": "designer", "name": "다인", "role": "대화 설계자", "color": "#b78ef0"},
    {"id": "writer", "name": "로운", "role": "응답 생성가", "color": "#6fd88a"},
    {"id": "reviewer", "name": "세아", "role": "품질 검수자", "color": "#f28ba8"},
]


def provider() -> dict | None:
    """설정된 환경변수로 LLM 공급자를 판별한다. 없으면 None(데모 모드)."""
    if os.environ.get("LLM_API_KEY") and os.environ.get("LLM_BASE_URL"):
        return {
            "name": "custom",
            "key": os.environ["LLM_API_KEY"],
            "base": os.environ["LLM_BASE_URL"],
            "model": os.environ.get("LLM_MODEL", ""),
        }
    if os.environ.get("GROQ_API_KEY"):
        return {
            "name": "groq",
            "key": os.environ["GROQ_API_KEY"],
            "base": "https://api.groq.com/openai/v1",
            "model": os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile"),
        }
    if os.environ.get("GEMINI_API_KEY"):
        return {
            "name": "gemini",
            "key": os.environ["GEMINI_API_KEY"],
            "base": "https://generativelanguage.googleapis.com/v1beta/openai",
            "model": os.environ.get("LLM_MODEL", "gemini-2.5-flash"),
        }
    return None


async def _chat(system: str, messages: list[dict], json_mode: bool = False,
                max_tokens: int = 1024) -> str:
    p = provider()
    payload: dict[str, Any] = {
        "model": p["model"],
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}] + messages,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=55) as client:
        r = await client.post(
            f"{p['base'].rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {p['key']}"},
            json=payload,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"] or ""


def _parse_json(text: str, fallback: dict) -> dict:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", text or "", re.S)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return fallback


def _ev(agent: str, state: str, activity: str = "") -> dict:
    return {"type": "agent", "agent": agent, "state": state, "activity": activity}


# ---------------------------------------------------------------- 데모 모드

_DEMO_REPLIES = [
    "안녕하세요! 저는 5명의 픽셀 에이전트가 함께 만드는 챗봇이에요. "
    "지금은 데모 모드라서 정해진 답변을 드리고 있어요. "
    "GROQ_API_KEY나 GEMINI_API_KEY(무료)를 설정하면 실제 LLM이 답변해 드립니다!",
    "왼쪽 오피스를 보시면 방금 누리(NLU) → 다인(설계) → 로운(생성) → 세아(검수) "
    "순서로 작업이 넘어간 걸 보실 수 있어요.",
    "데모 모드에서도 파이프라인은 진짜와 똑같이 돌아가요. "
    "무료 LLM 키만 있으면 이 자리에 실제 답변이 들어갑니다.",
]


async def _demo(message: str, turn: int) -> AsyncIterator[dict]:
    async def step(agent_id, thinking, working, done, secs):
        yield _ev(agent_id, "thinking", thinking)
        await asyncio.sleep(secs * 0.4)
        yield _ev(agent_id, "working", working)
        await asyncio.sleep(secs * 0.6)
        yield _ev(agent_id, "done", done)

    yield _ev("orchestrator", "working", "작업 분배 중…")
    async for e in step("nlu", "발화 읽는 중…", "의도 분석 중…", "의도: 일반 대화", 1.6):
        yield e
    async for e in step("designer", "전략 고민 중…", "응답 설계 중…", "톤: 친근함", 1.4):
        yield e
    async for e in step("writer", "초안 구상 중…", "응답 작성 중…", "초안 완성", 2.0):
        yield e
    async for e in step("reviewer", "초안 검토 중…", "품질 검수 중…", "승인 ✔", 1.4):
        yield e
    yield _ev("orchestrator", "done", "턴 완료")
    yield {
        "type": "reply",
        "reply": _DEMO_REPLIES[turn % len(_DEMO_REPLIES)],
        "demo": True,
        "nlu": {"intent": "일반 대화", "sentiment": "중립"},
        "review": {"approved": True, "feedback": "데모 모드 자동 승인"},
    }


# ---------------------------------------------------------------- 파이프라인


async def run_pipeline(history: list[dict], message: str) -> AsyncIterator[dict]:
    """사용자 메시지 하나를 5-에이전트 파이프라인으로 처리한다.

    history: [{"role": "user"|"assistant", "content": str}, ...] (클라이언트가 유지)
    """
    if provider() is None:
        turn = sum(1 for m in history if m.get("role") == "user")
        async for e in _demo(message, turn):
            yield e
        return

    try:
        yield _ev("orchestrator", "working", "작업 분배 중…")

        yield _ev("nlu", "thinking", "발화 분석 중…")
        nlu = _parse_json(
            await _chat(
                "당신은 챗봇 팀의 NLU 분석가입니다. 사용자 발화를 분석해 JSON만 출력하세요. "
                '형식: {"intent": "핵심 의도(짧은 한국어 구)", "entities": ["개체", ...], '
                '"sentiment": "긍정|중립|부정", "summary": "요약 한 문장"}',
                [{"role": "user", "content": message}],
                json_mode=True,
            ),
            {"intent": "일반 대화", "entities": [], "sentiment": "중립", "summary": message[:40]},
        )
        yield _ev("nlu", "done", f"의도: {nlu.get('intent', '?')}")

        yield _ev("designer", "thinking", "응답 전략 설계 중…")
        design = _parse_json(
            await _chat(
                "당신은 챗봇 팀의 대화 설계자입니다. NLU 분석을 바탕으로 응답 전략을 JSON만으로 출력하세요. "
                '형식: {"tone": "응답 톤", "strategy": "전략 한 문장", "key_points": ["포인트", ...]}',
                [{
                    "role": "user",
                    "content": f"사용자 발화: {message}\n\nNLU 분석: {json.dumps(nlu, ensure_ascii=False)}",
                }],
                json_mode=True,
            ),
            {"tone": "친근함", "strategy": "간결하고 자연스럽게 답한다", "key_points": []},
        )
        yield _ev("designer", "done", f"톤: {design.get('tone', '?')}")

        yield _ev("writer", "working", "응답 작성 중…")
        draft = await _chat(
            "당신은 챗봇 팀의 응답 생성가입니다. 대화 설계자의 전략에 따라 한국어로 응답을 작성합니다.\n"
            f"톤: {design.get('tone', '')}\n전략: {design.get('strategy', '')}\n"
            f"핵심 포인트: {', '.join(design.get('key_points', []))}\n"
            f"사용자 의도: {nlu.get('intent', '')} / 감정: {nlu.get('sentiment', '')}\n"
            "간결하고 자연스럽게 답하세요.",
            history[-10:] + [{"role": "user", "content": message}],
            max_tokens=2048,
        )
        yield _ev("writer", "done", "초안 완성")

        yield _ev("reviewer", "thinking", "품질 검수 중…")
        review = _parse_json(
            await _chat(
                "당신은 챗봇 팀의 품질 검수자입니다. 응답 초안의 정확성/톤/안전성을 검수하고 JSON만 출력하세요. "
                '형식: {"approved": true|false, "final_reply": "최종 응답(문제 있으면 수정본, 없으면 초안 그대로)", '
                '"feedback": "검수 코멘트 한 문장"}',
                [{"role": "user", "content": f"사용자 발화: {message}\n\n응답 초안:\n{draft}"}],
                json_mode=True,
                max_tokens=2048,
            ),
            {"approved": True, "final_reply": draft, "feedback": "자동 승인"},
        )
        yield _ev("reviewer", "done", "승인 ✔" if review.get("approved") else "수정 후 승인")

        yield _ev("orchestrator", "done", "턴 완료")
        yield {
            "type": "reply",
            "reply": review.get("final_reply") or draft,
            "demo": False,
            "nlu": nlu,
            "design": design,
            "review": {"approved": review.get("approved", True), "feedback": review.get("feedback", "")},
        }
    except Exception as exc:  # API 오류는 오피스에 표시하고 사용자에게 알림
        yield _ev("orchestrator", "error", f"오류: {type(exc).__name__}")
        yield {
            "type": "reply",
            "reply": f"죄송해요, 응답 생성 중 오류가 발생했어요. ({type(exc).__name__}: {exc})",
            "error": str(exc),
        }
