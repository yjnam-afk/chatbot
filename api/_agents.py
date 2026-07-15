"""멀티 에이전트 메이커 팀 파이프라인 (Vercel 서버리스 + 무료 LLM 대응).

harness-100 패턴의 5인 팀이 실제 작업물을 만든다:
팀장(코디) → 기획(누리) → 디자인(다인) → 개발(로운) → QA(세아)

- "~만들어줘" 요청이면: 기획 → 디자인 → 개발(단일 HTML 파일 생성) → QA → 작업물 전달
- 일반 질문/대화면: 기획자가 판별 후 개발자가 바로 답변 (호출 2회)

OpenAI 호환 chat/completions API 사용. 키 환경변수로 공급자 자동 인식:
  GROQ_API_KEY / GEMINI_API_KEY / (LLM_API_KEY + LLM_BASE_URL + LLM_MODEL)
아무 키도 없으면 데모 모드.

`run_pipeline`은 async generator — {type:"agent"|"talk"} 이벤트를 yield하고
마지막에 {type:"reply", reply, artifact?}를 yield한다. (서버리스: 상태는 응답
스트림에, 대화 이력은 클라이언트에)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, AsyncIterator

import httpx

AGENTS = [
    {"id": "orchestrator", "name": "코디", "role": "팀장", "color": "#f2b544"},
    {"id": "nlu", "name": "누리", "role": "기획자", "color": "#5bc8f5"},
    {"id": "designer", "name": "다인", "role": "디자이너", "color": "#b78ef0"},
    {"id": "writer", "name": "로운", "role": "개발자", "color": "#6fd88a"},
    {"id": "reviewer", "name": "세아", "role": "QA", "color": "#f28ba8"},
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


def _extract_html(text: str) -> str:
    """LLM 출력에서 단일 HTML 문서를 추출한다."""
    m = re.search(r"```(?:html)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    m = re.search(r"(<!DOCTYPE.*?</html\s*>)", text, re.S | re.I)
    if m:
        return m.group(1).strip()
    low = text.lower()
    if "<html" in low:
        return text[low.index("<html"):].strip()
    return text.strip()


def _ev(agent: str, state: str, activity: str = "") -> dict:
    return {"type": "agent", "agent": agent, "state": state, "activity": activity}


def _talk(agent: str, text: str) -> dict:
    """에이전트가 팀 동료에게 하는 말 — 광장 말풍선/팀 대화 피드에 표시된다."""
    return {"type": "talk", "agent": agent, "text": str(text)[:120]}


BUILD_WORDS = ("만들", "제작", "게임", "페이지", "사이트", "웹앱", "앱 ", "툴 ", "계산기", "타이머")


# ---------------------------------------------------------------- 데모 모드

_DEMO_HTML = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><title>데모 작업물</title>
<style>body{font-family:sans-serif;display:flex;flex-direction:column;align-items:center;
justify-content:center;height:100vh;margin:0;background:linear-gradient(160deg,#7ebf5a,#4f9e4f);color:#fff}
h1{text-shadow:0 2px 0 rgba(0,0,0,.2)}button{font-size:22px;padding:14px 28px;border:none;
border-radius:14px;background:#fdf6dd;color:#6b5537;cursor:pointer;box-shadow:0 4px 0 rgba(0,0,0,.15)}
button:active{transform:translateY(3px);box-shadow:none}</style></head>
<body><h1>🍃 픽셀 사무소 데모 작업물</h1><p>LLM 키를 넣으면 진짜 요청한 걸 만들어드려요!</p>
<button onclick="this.textContent='🍀 '+(++window.n||(window.n=1))+'번 눌렀어요!'">눌러보세요</button>
</body></html>"""


async def _demo(message: str) -> AsyncIterator[dict]:
    async def step(agent_id, thinking, working, done, secs):
        yield _ev(agent_id, "thinking", thinking)
        await asyncio.sleep(secs * 0.4)
        yield _ev(agent_id, "working", working)
        await asyncio.sleep(secs * 0.6)
        yield _ev(agent_id, "done", done)

    build = any(w in message for w in BUILD_WORDS)
    yield _ev("orchestrator", "working", "작업 분배 중…")
    yield _talk("orchestrator", "새 의뢰 도착! 다들 모여주세요 🍃")
    async for e in step("nlu", "의뢰 읽는 중…", "요구사항 정리 중…", "기획 완료", 1.5):
        yield e
    if build:
        yield _talk("nlu", "요구사항 정리했어요! 다인님, 디자인 부탁해요.")
        async for e in step("designer", "레이아웃 구상 중…", "디자인 설계 중…", "디자인 완료", 1.5):
            yield e
        yield _talk("designer", "산뜻한 그린 톤으로 갈게요. 로운님, 개발 고고!")
        async for e in step("writer", "코드 구상 중…", "코딩 중…", "구현 완료", 2.2):
            yield e
        yield _talk("writer", "다 짰어요! 세아님 테스트 부탁해요 🛠")
        async for e in step("reviewer", "코드 읽는 중…", "테스트 중…", "QA 통과", 1.5):
            yield e
        yield _talk("reviewer", "버튼도 잘 눌리고 이상 없어요. 출고! ✅")
        yield _ev("orchestrator", "done", "납품 완료")
        yield _talk("orchestrator", "작업물 전달 완료! 다들 수고했어요 ☕")
        yield {
            "type": "reply",
            "reply": "데모 작업물을 만들어봤어요! 🎁 미리보기를 눌러 확인해보세요.\n"
                     "GROQ_API_KEY(무료)를 설정하면 요청하신 걸 진짜로 만들어드립니다.",
            "demo": True,
            "artifact": {"title": "데모 작업물", "html": _DEMO_HTML},
        }
    else:
        yield _talk("nlu", "이건 그냥 질문이네요. 로운님이 바로 답할게요!")
        async for e in step("writer", "답변 구상 중…", "답변 작성 중…", "답변 완료", 1.8):
            yield e
        yield _talk("writer", "답변 보냈어요!")
        yield _ev("orchestrator", "done", "턴 완료")
        yield {
            "type": "reply",
            "reply": "지금은 데모 모드예요! 🍃 GROQ_API_KEY(무료)를 설정하면 실제 LLM이 답변하고, "
                     "\"테트리스 만들어줘\" 같은 의뢰를 하면 진짜 동작하는 웹앱을 만들어드려요.",
            "demo": True,
        }


# ---------------------------------------------------------------- 파이프라인


async def run_pipeline(history: list[dict], message: str) -> AsyncIterator[dict]:
    """사용자 메시지 하나를 5인 메이커 팀 파이프라인으로 처리한다."""
    if provider() is None:
        async for e in _demo(message):
            yield e
        return

    try:
        yield _ev("orchestrator", "working", "작업 분배 중…")
        yield _talk("orchestrator", "새 의뢰 도착! 누리님, 기획 먼저 부탁해요 🍃")

        # ---- 기획 (누리)
        yield _ev("nlu", "thinking", "요구사항 분석 중…")
        recent = " / ".join(m.get("content", "")[:60] for m in history[-4:])
        plan = _parse_json(
            await _chat(
                "당신은 메이커 팀의 기획자 '누리'입니다. 사용자 의뢰를 분석해 JSON만 출력하세요. "
                "무언가 만들어달라는 요청(웹페이지, 게임, 앱, 도구 등)이면 type을 build로, "
                "일반 질문/대화면 chat으로 판별합니다. 형식: "
                '{"type": "build|chat", "title": "작업물 이름(build일 때, 짧게)", '
                '"requirements": ["구체적 요구사항", ...], '
                '"say": "팀 동료들에게 기획 내용을 전하는 짧은 구어체 한 마디"}',
                [{"role": "user", "content": f"이전 대화: {recent}\n\n의뢰: {message}"}],
                json_mode=True,
            ),
            {"type": "chat", "title": "", "requirements": []},
        )
        is_build = plan.get("type") == "build"
        yield _ev("nlu", "done", f"기획: {plan.get('title') or '일반 문의'}")
        yield _talk("nlu", plan.get("say") or ("요구사항 정리했어요!" if is_build else "이건 질문이네요, 로운님이 바로 답할게요!"))

        if not is_build:
            # ---- 일반 대화: 개발자가 바로 답변
            yield _ev("writer", "working", "답변 작성 중…")
            reply = await _chat(
                "당신은 메이커 팀의 개발자 '로운'입니다. 친절하고 간결한 한국어로 답하세요. "
                "당신의 팀은 '~만들어줘' 의뢰를 받으면 실제 동작하는 웹앱을 만들어주는 팀입니다.",
                history[-10:] + [{"role": "user", "content": message}],
                max_tokens=2048,
            )
            yield _ev("writer", "done", "답변 완료")
            yield _talk("writer", "답변 보냈어요!")
            yield _ev("orchestrator", "done", "턴 완료")
            yield {"type": "reply", "reply": reply, "plan": plan}
            return

        # ---- 제작 파이프라인
        title = plan.get("title") or "새 작업물"
        reqs = plan.get("requirements") or [message]

        # 디자인 (다인)
        yield _ev("designer", "thinking", "디자인 설계 중…")
        design = _parse_json(
            await _chat(
                "당신은 메이커 팀의 디자이너 '다인'입니다. 웹 작업물의 디자인 명세를 JSON만으로 출력하세요. "
                '형식: {"layout": "화면 구성 설명", "style": "색/폰트/무드", '
                '"features": ["UX 디테일", ...], "say": "개발자 로운에게 디자인을 전달하는 짧은 한 마디"}',
                [{"role": "user", "content": f"작업물: {title}\n요구사항: {json.dumps(reqs, ensure_ascii=False)}"}],
                json_mode=True,
            ),
            {"layout": "단일 화면", "style": "깔끔하고 밝은 스타일", "features": []},
        )
        yield _ev("designer", "done", "디자인 완료")
        yield _talk("designer", design.get("say") or "디자인 넘겼어요. 로운님 부탁해요!")

        # 개발 (로운)
        yield _ev("writer", "working", "코딩 중…")
        raw = await _chat(
            "당신은 숙련된 프론트엔드 개발자 '로운'입니다. 요구사항과 디자인 명세에 따라 "
            "완전한 단일 HTML 파일을 작성하세요. 규칙:\n"
            "- 외부 리소스 없이 인라인 <style>과 <script>만 사용\n"
            "- 실제로 동작해야 함 (게임이면 플레이 가능하게)\n"
            "- UI 텍스트는 한국어\n"
            "- 모바일에서도 보이도록 반응형\n"
            "- 코드만 출력 (```html 펜스 사용 가능, 설명 금지)",
            [{
                "role": "user",
                "content": (
                    f"작업물: {title}\n"
                    f"요구사항: {json.dumps(reqs, ensure_ascii=False)}\n"
                    f"디자인: {json.dumps({k: design.get(k) for k in ('layout', 'style', 'features')}, ensure_ascii=False)}"
                ),
            }],
            max_tokens=8000,
        )
        html = _extract_html(raw)
        lines = html.count("\n") + 1
        yield _ev("writer", "done", f"구현 완료 ({lines}줄)")
        yield _talk("writer", f"코드 {lines}줄 완성! 세아님 테스트 부탁해요 🛠")

        # QA (세아)
        yield _ev("reviewer", "thinking", "테스트 중…")
        review = _parse_json(
            await _chat(
                "당신은 메이커 팀의 QA '세아'입니다. HTML 코드를 검토해 JSON만 출력하세요. "
                '형식: {"approved": true|false, "issues": ["발견한 문제", ...], '
                '"say": "팀에게 검수 결과를 알리는 짧은 한 마디"}',
                [{
                    "role": "user",
                    "content": f"요구사항: {json.dumps(reqs, ensure_ascii=False)}\n\n코드:\n{html[:6000]}",
                }],
                json_mode=True,
            ),
            {"approved": True, "issues": []},
        )
        yield _ev("reviewer", "done", "QA 통과" if review.get("approved") else "이슈 발견")
        yield _talk("reviewer", review.get("say") or "테스트 끝! 출고해도 되겠어요 ✅")

        yield _ev("orchestrator", "done", "납품 완료")
        yield _talk("orchestrator", "작업물 전달 완료! 다들 수고했어요 ☕")

        issues = review.get("issues") or []
        note = f"\n\nQA 메모: {' / '.join(str(i) for i in issues[:3])}" if issues else ""
        yield {
            "type": "reply",
            "reply": f"'{title}' 완성했어요! 🎁 미리보기 버튼으로 바로 확인해보세요.{note}",
            "plan": plan,
            "review": review,
            "artifact": {"title": title, "html": html},
        }
    except Exception as exc:
        yield _ev("orchestrator", "error", f"오류: {type(exc).__name__}")
        yield {
            "type": "reply",
            "reply": f"죄송해요, 작업 중 오류가 발생했어요. ({type(exc).__name__}: {exc})",
            "error": str(exc),
        }
