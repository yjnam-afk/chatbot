"""멀티 에이전트 챗봇 파이프라인.

harness-100의 38-chatbot-builder 패턴을 따라 5개 에이전트가 협업한다:
오케스트레이터 → NLU 분석가 → 대화 설계자 → 응답 생성가 → 품질 검수자

ANTHROPIC_API_KEY(또는 ANTHROPIC_AUTH_TOKEN)가 없으면 데모 모드로 동작하여
API 호출 없이도 파이프라인/시각화가 그대로 재현된다.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Awaitable, Callable

try:
    import anthropic
    from anthropic import AsyncAnthropic
except ImportError:  # 패키지 미설치 시에도 데모 모드로 구동 가능
    anthropic = None
    AsyncAnthropic = None

MODEL = os.environ.get("CHATBOT_MODEL", "claude-opus-4-8")

AGENTS = [
    {"id": "orchestrator", "name": "코디", "role": "오케스트레이터", "color": "#f2b544"},
    {"id": "nlu", "name": "누리", "role": "NLU 분석가", "color": "#5bc8f5"},
    {"id": "designer", "name": "다인", "role": "대화 설계자", "color": "#b78ef0"},
    {"id": "writer", "name": "로운", "role": "응답 생성가", "color": "#6fd88a"},
    {"id": "reviewer", "name": "세아", "role": "품질 검수자", "color": "#f28ba8"},
]

# emit(agent_id, state, activity) — state: idle | thinking | working | done | error
Emit = Callable[[str, str, str], Awaitable[None]]


def llm_available() -> bool:
    if anthropic is None:
        return False
    return bool(
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    )


_client: "AsyncAnthropic | None" = None


def client() -> "AsyncAnthropic":
    global _client
    if _client is None:
        _client = AsyncAnthropic()
    return _client


NLU_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "description": "사용자 발화의 핵심 의도 (짧은 한국어 구)"},
        "entities": {"type": "array", "items": {"type": "string"}},
        "sentiment": {"type": "string", "enum": ["긍정", "중립", "부정"]},
        "summary": {"type": "string", "description": "발화 요약 한 문장"},
    },
    "required": ["intent", "entities", "sentiment", "summary"],
    "additionalProperties": False,
}

DESIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "tone": {"type": "string", "description": "응답 톤 (예: 친근한, 전문적인)"},
        "strategy": {"type": "string", "description": "응답 전략 한 문장"},
        "key_points": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tone", "strategy", "key_points"],
    "additionalProperties": False,
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "final_reply": {"type": "string", "description": "사용자에게 전달할 최종 응답 (필요 시 수정본)"},
        "feedback": {"type": "string", "description": "검수 코멘트 한 문장"},
    },
    "required": ["approved", "final_reply", "feedback"],
    "additionalProperties": False,
}


def _first_text(response) -> str:
    for block in response.content:
        if block.type == "text":
            return block.text
    return ""


async def _structured(system: str, user: str, schema: dict, max_tokens: int = 1024) -> dict:
    response = await client().messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": user}],
    )
    return json.loads(_first_text(response))


async def _nlu(message: str) -> dict:
    return await _structured(
        "당신은 챗봇 팀의 NLU 분석가입니다. 사용자 발화의 의도, 개체, 감정을 분석합니다.",
        f"다음 사용자 발화를 분석하세요:\n\n{message}",
        NLU_SCHEMA,
    )


async def _design(message: str, nlu: dict) -> dict:
    return await _structured(
        "당신은 챗봇 팀의 대화 설계자입니다. NLU 분석 결과를 바탕으로 응답 전략을 설계합니다.",
        f"사용자 발화: {message}\n\nNLU 분석: {json.dumps(nlu, ensure_ascii=False)}\n\n응답 전략을 설계하세요.",
        DESIGN_SCHEMA,
    )


async def _write(history: list[dict], message: str, nlu: dict, design: dict) -> str:
    system = (
        "당신은 챗봇 팀의 응답 생성가입니다. 대화 설계자의 전략에 따라 한국어로 응답을 작성합니다.\n"
        f"톤: {design['tone']}\n전략: {design['strategy']}\n"
        f"핵심 포인트: {', '.join(design['key_points'])}\n"
        f"사용자 의도: {nlu['intent']} / 감정: {nlu['sentiment']}\n"
        "간결하고 자연스럽게 답하세요."
    )
    messages = history[-10:] + [{"role": "user", "content": message}]
    response = await client().messages.create(
        model=MODEL,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=system,
        messages=messages,
    )
    return _first_text(response)


async def _review(message: str, draft: str) -> dict:
    return await _structured(
        "당신은 챗봇 팀의 품질 검수자입니다. 응답 초안의 정확성/톤/안전성을 검수하고, "
        "문제가 있으면 final_reply에 수정본을 담습니다. 문제가 없으면 초안을 그대로 담습니다.",
        f"사용자 발화: {message}\n\n응답 초안:\n{draft}",
        REVIEW_SCHEMA,
        max_tokens=4096,
    )


# ---------------------------------------------------------------- 데모 모드

_DEMO_REPLIES = [
    "안녕하세요! 저는 5명의 픽셀 에이전트가 함께 만드는 챗봇이에요. "
    "지금은 데모 모드라서 정해진 답변을 드리고 있어요. "
    "ANTHROPIC_API_KEY를 설정하면 Claude가 실제로 답변해 드립니다!",
    "왼쪽 오피스를 보시면 방금 누리(NLU) → 다인(설계) → 로운(생성) → 세아(검수) "
    "순서로 작업이 넘어간 걸 보실 수 있어요.",
    "데모 모드에서도 파이프라인은 진짜와 똑같이 돌아가요. "
    "API 키만 있으면 이 자리에 Claude의 실제 답변이 들어갑니다.",
]
_demo_counter = 0


async def _demo_pipeline(message: str, emit: Emit) -> dict[str, Any]:
    global _demo_counter

    async def step(agent_id: str, thinking: str, working: str, done: str, secs: float):
        await emit(agent_id, "thinking", thinking)
        await asyncio.sleep(secs * 0.4)
        await emit(agent_id, "working", working)
        await asyncio.sleep(secs * 0.6)
        await emit(agent_id, "done", done)

    await emit("orchestrator", "working", "작업 분배 중…")
    await step("nlu", "발화 읽는 중…", "의도 분석 중…", "의도: 일반 대화", 1.6)
    await step("designer", "전략 고민 중…", "응답 설계 중…", "톤: 친근함", 1.4)
    await step("writer", "초안 구상 중…", "응답 작성 중…", "초안 완성", 2.0)
    await step("reviewer", "초안 검토 중…", "품질 검수 중…", "승인 ✔", 1.4)

    reply = _DEMO_REPLIES[_demo_counter % len(_DEMO_REPLIES)]
    _demo_counter += 1
    return {
        "reply": reply,
        "demo": True,
        "nlu": {"intent": "일반 대화", "entities": [], "sentiment": "중립", "summary": message[:40]},
        "review": {"approved": True, "feedback": "데모 모드 자동 승인"},
    }


# ---------------------------------------------------------------- 파이프라인


async def run_pipeline(history: list[dict], message: str, emit: Emit) -> dict[str, Any]:
    """사용자 메시지 하나를 5-에이전트 파이프라인으로 처리한다."""
    if not llm_available():
        result = await _demo_pipeline(message, emit)
        await emit("orchestrator", "done", "턴 완료")
        return result

    try:
        await emit("orchestrator", "working", "작업 분배 중…")

        await emit("nlu", "thinking", "발화 분석 중…")
        nlu = await _nlu(message)
        await emit("nlu", "done", f"의도: {nlu['intent']}")

        await emit("designer", "thinking", "응답 전략 설계 중…")
        design = await _design(message, nlu)
        await emit("designer", "done", f"톤: {design['tone']}")

        await emit("writer", "working", "응답 작성 중…")
        draft = await _write(history, message, nlu, design)
        await emit("writer", "done", "초안 완성")

        await emit("reviewer", "thinking", "품질 검수 중…")
        review = await _review(message, draft)
        await emit(
            "reviewer", "done", "승인 ✔" if review["approved"] else "수정 후 승인"
        )

        await emit("orchestrator", "done", "턴 완료")
        return {
            "reply": review["final_reply"] or draft,
            "demo": False,
            "nlu": nlu,
            "design": design,
            "review": {"approved": review["approved"], "feedback": review["feedback"]},
        }
    except Exception as exc:  # API 오류는 오피스에 오류 상태로 표시하고 사용자에게 알림
        await emit("orchestrator", "error", f"오류: {type(exc).__name__}")
        return {
            "reply": f"죄송해요, 응답 생성 중 오류가 발생했어요. ({type(exc).__name__}: {exc})",
            "demo": False,
            "error": str(exc),
        }
