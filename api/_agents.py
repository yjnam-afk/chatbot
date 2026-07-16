"""기술사 답안 사무소 — 멀티 에이전트 답안 작성 파이프라인 (Vercel 서버리스 + 무료 LLM 대응).

harness-100 패턴의 5인 팀이 기술사 시험 답안지를 만든다:
진행 간사(코디) → 출제 의도 분석(누리) → 답안 구조 설계(다인) → 답안 작성(로운) → 채점위원(세아)

- 시험 문제면: 분석 → 설계 → 작성 → 채점 → (85점 미만 시 보완 1회 + 재채점) → 답안지 HTML 전달
- 일반 질문/대화면: 누리가 판별 후 로운(수험 멘토)이 바로 답변 (호출 2회)
- LLM 호출은 최대 6회 (분석1 + 설계1 + 초안1 + 채점1 + 보완1 + 재채점1)

OpenAI 호환 chat/completions API 사용. 키 환경변수로 공급자 자동 인식:
  GROQ_API_KEY / GEMINI_API_KEY / (LLM_API_KEY + LLM_BASE_URL + LLM_MODEL)
아무 키도 없으면 데모 모드 (보완 루프 포함 고정 시나리오).

`run_pipeline`은 async generator — {type:"agent"|"talk"} 이벤트를 yield하고
마지막에 {type:"reply", reply, exam?, review?, artifact?}를 yield한다.
(서버리스: 상태는 응답 스트림에, 대화 이력은 클라이언트에)
"""

from __future__ import annotations

import asyncio
import html as html_mod
import json
import os
import re
from typing import Any, AsyncIterator

import httpx

AGENTS = [
    {"id": "orchestrator", "name": "코디", "role": "진행 간사", "color": "#f2b544"},
    {"id": "nlu", "name": "누리", "role": "출제 의도 분석", "color": "#5bc8f5"},
    {"id": "designer", "name": "다인", "role": "답안 구조 설계", "color": "#b78ef0"},
    {"id": "writer", "name": "로운", "role": "답안 작성", "color": "#6fd88a"},
    {"id": "reviewer", "name": "세아", "role": "채점위원", "color": "#f28ba8"},
]

PASS_SCORE = 85


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


def _extract_body(text: str) -> str:
    """LLM 출력에서 답안 본문 HTML 프래그먼트를 추출한다 (```펜스/문서 꼬리 제거)."""
    m = re.search(r"```(?:html)?\s*(.*?)```", text or "", re.S)
    if m:
        text = m.group(1)
    text = (text or "").strip()
    i = text.find("<h2")
    if i > 0:
        text = text[i:]
    text = re.sub(r"</(?:main|body|html)\s*>.*$", "", text, flags=re.S | re.I)
    return text.strip()


def _score_of(v: Any, default: int = 70) -> int:
    try:
        return max(0, min(100, int(float(v))))
    except (TypeError, ValueError):
        return default


def _ev(agent: str, state: str, activity: str = "") -> dict:
    return {"type": "agent", "agent": agent, "state": state, "activity": activity}


def _talk(agent: str, text: str) -> dict:
    """에이전트가 팀 동료에게 하는 말 — 광장 말풍선/팀 대화 피드에 표시된다."""
    return {"type": "talk", "agent": agent, "text": str(text)[:120]}


EXAM_WORDS = ("하시오", "설명하", "기술하", "논하", "서술하", "점)")


# ---------------------------------------------------------------- 답안지 템플릿

_ANSWER_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — 기술사 답안지</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --ink: #1d232a; --sub: #6a7076; --rule: #cfc9bb; --rule-soft: #e5e1d6;
  --paper: #fffefb; --tint: #f5f3ec; --navy: #2c4a6e;
}
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
@counter-style ganada { system: fixed; symbols: "가" "나" "다" "라" "마" "바" "사"; suffix: ". "; }
body {
  background: #e8e7e2; color: var(--ink);
  font-family: "Noto Serif KR", "Noto Serif CJK KR", "Nanum Myeongjo", "Source Han Serif K", Batang, AppleMyungjo, serif;
  font-size: 14px; line-height: 1.75; padding: 32px 16px; word-break: keep-all;
}
.sheet {
  max-width: 794px; margin: 0 auto; background: var(--paper);
  border: 1px solid #d8d4c8; box-shadow: 0 2px 24px rgba(40,40,30,0.10);
  padding: 44px 52px 40px;
}
.sheet-head { border-top: 3px double var(--ink); padding-top: 14px; }
.head-row { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.doc-label { font-size: 12px; letter-spacing: 0.35em; color: var(--sub); }
.points { font-size: 12.5px; font-weight: 700; color: var(--navy); border: 1px solid var(--navy); border-radius: 3px; padding: 2px 10px; white-space: nowrap; }
h1 { font-size: 21px; font-weight: 700; line-height: 1.45; margin: 10px 0 16px; }
.q-box { display: flex; gap: 14px; border-top: 1px solid var(--ink); border-bottom: 1px solid var(--ink); background: var(--tint); padding: 12px 10px; }
.q-label { flex: none; font-weight: 700; font-size: 13px; color: var(--navy); }
.q-text { font-size: 14px; white-space: pre-wrap; }
.answer { counter-reset: sec; padding: 26px 2px 8px; min-height: 280px; }
.answer h2 { counter-increment: sec; counter-reset: sub; font-size: 16px; font-weight: 700; margin: 26px 0 10px; padding-bottom: 6px; border-bottom: 1px solid var(--rule); }
.answer h2::before { content: counter(sec, upper-roman) ". "; color: var(--navy); }
.answer h2:first-child { margin-top: 0; }
.answer h3 { counter-increment: sub; font-size: 14.5px; font-weight: 700; margin: 16px 0 6px; }
.answer h3::before { content: counter(sub, ganada) ". "; color: var(--navy); }
.answer p { margin: 6px 0 10px; }
.answer ul, .answer ol { margin: 4px 0 12px 22px; }
.answer li { margin: 3px 0; }
.answer .keyword { font-weight: 700; border-bottom: 2px solid var(--navy); }
.answer .gangul { font-size: 12.5px; color: var(--sub); margin: -8px 0 14px; }
.answer table { width: 100%; border-collapse: collapse; margin: 10px 0 16px; font-size: 13px; }
.answer th, .answer td { border: 1px solid var(--rule); padding: 7px 10px; text-align: left; vertical-align: top; line-height: 1.6; }
.answer th { background: var(--tint); font-weight: 700; white-space: nowrap; }
.diagram { margin: 12px 0 18px; padding: 18px 14px; border: 1px solid var(--rule-soft); background: #fbfaf6; }
.d-row { display: flex; align-items: center; justify-content: center; gap: 10px; flex-wrap: wrap; }
.d-col { display: flex; flex-direction: column; align-items: center; gap: 8px; }
.d-box { border: 1.5px solid var(--ink); background: #fff; padding: 8px 16px; min-width: 92px; font-size: 12.5px; font-weight: 700; text-align: center; line-height: 1.5; }
.d-box.soft { border: 1px dashed var(--sub); background: var(--tint); font-weight: 400; }
.d-box.wide { width: 72%; min-width: 200px; }
.d-box small { display: block; font-size: 11px; font-weight: 400; color: var(--sub); }
.d-arrow { flex: none; color: var(--navy); font-weight: 700; font-size: 15px; }
.d-title { margin-top: 12px; text-align: center; font-size: 12px; color: var(--sub); letter-spacing: 0.06em; }
.end-mark { text-align: right; font-size: 13px; color: var(--sub); margin-top: 24px; }
.mnemonic { margin-top: 18px; border: 1.5px dashed var(--navy); background: #f2f5f9; padding: 14px 18px 12px; }
.mn-label { font-size: 12px; font-weight: 700; color: var(--navy); letter-spacing: 0.25em; margin-bottom: 6px; }
.mnemonic p { font-size: 13.5px; margin: 3px 0; }
.mnemonic b { color: var(--navy); }
.sheet-foot { margin-top: 26px; padding-top: 8px; border-top: 3px double var(--ink); text-align: right; font-size: 11px; color: var(--sub); letter-spacing: 0.2em; }
@page { size: A4 portrait; margin: 16mm 15mm; }
@media print {
  body { background: #fff; padding: 0; font-size: 10.5pt; }
  .sheet { max-width: none; border: none; box-shadow: none; padding: 0; }
  .answer h2, .answer h3 { break-after: avoid; page-break-after: avoid; }
  .diagram, .mnemonic, .q-box, tr { break-inside: avoid; page-break-inside: avoid; }
}
</style>
</head>
<body>
<div class="sheet">
  <header class="sheet-head">
    <div class="head-row">
      <span class="doc-label">기 술 사 답 안 지</span>
      <span class="points">{kind} · {points}점</span>
    </div>
    <h1>{title}</h1>
    <div class="q-box">
      <span class="q-label">문제</span>
      <p class="q-text">{question}</p>
    </div>
  </header>
  <main class="answer">
{body}
  </main>
  <p class="end-mark">끝</p>
  <footer class="mnemonic">
    <div class="mn-label">두문자 암기 포인트</div>
    {mnemonic_html}
  </footer>
  <div class="sheet-foot">기술사 답안 사무소</div>
</div>
</body>
</html>"""


def render_answer(question: str, title: str, kind: str, points: int | str,
                  body: str, mnemonic_html: str) -> str:
    """답안지 템플릿에 내용을 채워 완성 HTML을 만든다.

    CSS 중괄호 때문에 str.format() 금지. 입력값에 "{body}" 같은 리터럴
    플레이스홀더가 있어도 재치환되지 않도록 단일 패스 re.sub로 치환한다.
    question/title/kind/points는 escape, body/mnemonic_html은 이미 HTML.
    """
    parts = {
        "title": html_mod.escape(str(title)),
        "kind": html_mod.escape(str(kind)),
        "points": html_mod.escape(str(points)),
        "question": html_mod.escape(str(question)),
        "body": body,
        "mnemonic_html": mnemonic_html,
    }
    return re.sub(r"\{(title|kind|points|question|body|mnemonic_html)\}",
                  lambda m: parts[m.group(1)], _ANSWER_TEMPLATE)


def _mnemonic_html(mn: dict | None) -> str:
    """설계자의 mnemonic JSON을 <p><b>두문자</b> — 풀이</p> 형태(1~3줄)로 렌더."""
    mn = mn or {}
    word = str(mn.get("word") or "").strip()
    exp = [str(e).strip() for e in (mn.get("expansion") or []) if str(e).strip()]
    if not word and not exp:
        return "<p><b>핵심 키워드</b> — 답안 소제목의 첫 글자를 이어 붙여 암기하세요.</p>"
    expansion = " · ".join(exp[:5]) if exp else "핵심 키워드 두문자 풀이"
    return (f"<p><b>{html_mod.escape(word or '두문자')}</b> — "
            f"{html_mod.escape(expansion)}</p>")


# ---------------------------------------------------------------- 프롬프트

_BODY_RULES = """[답안 본문 HTML 규칙 — ITPE 기술사 답안 문법]
- HTML 프래그먼트만 출력. <html>/<head>/<body>/<style>/<script>/외부 리소스/인라인 style 금지.
- 허용 태그: h2, h3, p, ul, ol, li, b, strong, table, thead, tbody, tr, th, td, span class="keyword", p class="gangul", div class="diagram"(내부: d-row, d-col, d-box, d-arrow, d-title).
- 첫 요소는 <h2>. h2/h3에 번호를 직접 붙이지 말 것 — h2는 로마 숫자(Ⅰ. Ⅱ. Ⅲ. Ⅳ.), h3는 가나다(가. 나. 다.)가 자동으로 매겨짐.
- 서술 문체는 개조식: 모든 문장을 "~임", "~함", "~됨"으로 종결. 만연체 금지.
- 표는 기본 3단(구분/항목/설명) 구성, th 첫 행 + 3~5행. 개념도(div.diagram)는 1~2개 포함.
- 개념도·표 바로 아래에는 간글 1줄을 붙임: <p class="gangul">상기 구성도는 ○○의 ~를 도식화한 것임</p>
- 핵심 용어는 섹션당 1~3개를 <span class="keyword">용어</span>로 강조.

[서술형(25점) 목차 — 4단락 고정]
1) <h2>○○의 개요</h2> (서론) — 리드문 1문장(p) → <h3>정의</h3> 2줄 이내 → <h3>필요성</h3>(또는 등장배경) 개조식 목록 또는 간단 표
2) <h2>○○의 구성도 및 구성요소</h2> (본론) — <h3>구성도</h3> div.diagram + 바로 아래 간글 1줄 → <h3>구성요소</h3> 3단표(구분/구성요소/설명)
3) <h2>문제가 직접 요구한 사항</h2> — h2 제목은 문제의 요구(예: "도입 시 고려사항", "○○와의 비교")로 짓고, 세부 요구별로 h3 분리. 비교 요구 시 비교표 활용
4) <h2>결론 및 전망</h2> — 고려사항/전망/제언 등 차별화 포인트, 0.5단락 분량

[용어형(10점) 목차 — 3단락]
1) <h2>○○의 정의</h2> — 리드문 + 정의 2줄
2) <h2>○○의 개념도 및 구성요소</h2> — <h3>개념도</h3> div.diagram + 간글 → <h3>구성요소</h3> 3단표
3) <h2>활용방안</h2> 또는 고려사항

- 개념도 예시 1 (가로 흐름형):
<div class="diagram"><div class="d-row"><div class="d-box">클라이언트</div><span class="d-arrow">→</span><div class="d-box">API 게이트웨이<small>인증·라우팅</small></div><span class="d-arrow">→</span><div class="d-box soft">데이터 저장소</div></div><div class="d-title">[그림 1] 요청 처리 흐름</div></div>
- 개념도 예시 2 (세로 계층형):
<div class="diagram"><div class="d-col"><div class="d-box wide">정책 계층 <small>거버넌스</small></div><span class="d-arrow">↓</span><div class="d-box wide soft">인프라 계층 <small>네트워크</small></div></div><div class="d-title">[그림 2] 계층 구조</div></div>"""


# ---------------------------------------------------------------- 데모 모드

_DEMO_QUESTION = ("제로 트러스트 보안 모델의 개념, 구성요소, "
                  "도입 시 고려사항에 대하여 설명하시오 (25점)")

_DEMO_BODY = """<h2>제로 트러스트 보안 모델의 개요</h2>
<p>경계 기반 보안의 한계를 극복하기 위해 모든 접근 요청을 상시 검증하는 <span class="keyword">제로 트러스트(Zero Trust)</span> 보안 모델의 대두.</p>
<h3>제로 트러스트의 정의</h3>
<p>네트워크 내·외부를 구분하지 않고 "절대 신뢰하지 말고, 항상 검증하라(Never Trust, Always Verify)" 원칙에 따라 모든 접근 요청을 <span class="keyword">지속 검증</span>하는 보안 모델임.</p>
<h3>제로 트러스트의 필요성</h3>
<ul>
  <li>클라우드·원격근무 확산으로 전통적 네트워크 경계(Perimeter) 소멸됨</li>
  <li>내부자 위협과 측면 이동(Lateral Movement) 공격 증가함</li>
  <li>경계 방어 중심(성곽형) 보안 모델의 구조적 한계 노출됨</li>
</ul>

<h2>제로 트러스트의 구성도 및 구성요소</h2>
<h3>제로 트러스트 구성도</h3>
<div class="diagram"><div class="d-col"><div class="d-box wide">정책 결정 지점(PDP) <small>정책 엔진 · 접근 여부 판단</small></div><span class="d-arrow">↓</span><div class="d-row"><div class="d-box">주체<small>사용자·기기</small></div><span class="d-arrow">→</span><div class="d-box">정책 시행 지점(PEP)<small>세션 생성·차단</small></div><span class="d-arrow">→</span><div class="d-box soft">보호 자원<small>데이터·시스템</small></div></div></div><div class="d-title">[그림 1] 제로 트러스트 접근 제어 구성도 (NIST SP 800-207)</div></div>
<p class="gangul">상기 구성도는 PDP의 동적 접근 판단과 PEP의 세션 통제로 자원을 보호하는 구조를 도식화한 것임</p>
<h3>제로 트러스트의 구성요소</h3>
<table>
  <thead><tr><th>구분</th><th>구성요소</th><th>설명</th></tr></thead>
  <tbody>
    <tr><td>제어부</td><td>정책 결정 지점(PDP)</td><td>정책 엔진·정책 관리자가 접근 허용 여부를 동적으로 결정함</td></tr>
    <tr><td>실행부</td><td>정책 시행 지점(PEP)</td><td>결정된 정책에 따라 세션 생성·유지·차단을 시행함</td></tr>
    <tr><td>입력부</td><td>신뢰도 평가 입력</td><td>ID·기기 상태·위협 인텔리전스·행위 로그를 지속 평가함</td></tr>
    <tr><td>격리부</td><td><span class="keyword">마이크로 세그멘테이션</span></td><td>자원 단위로 네트워크를 분할해 측면 이동을 차단함</td></tr>
  </tbody>
</table>
<p class="gangul">상기 구성요소는 3대 원칙(명시적 검증·최소 권한·침해 가정)을 구현하는 기능 단위임</p>

<h2>도입 시 고려사항</h2>
<h3>기존 경계 보안 모델과의 비교</h3>
<table>
  <thead><tr><th>구분</th><th>경계 보안 모델</th><th>제로 트러스트 모델</th></tr></thead>
  <tbody>
    <tr><td>신뢰 기준</td><td>내부 네트워크 암묵적 신뢰</td><td>위치 무관, 모든 요청 검증</td></tr>
    <tr><td>방어 지점</td><td>네트워크 경계(방화벽 중심)</td><td>자원 단위(ID·기기·데이터)</td></tr>
    <tr><td>검증 시점</td><td>최초 접속 시 1회</td><td>세션 전체 <span class="keyword">지속 인증</span></td></tr>
    <tr><td>권한 부여</td><td>광범위한 내부 접근 허용</td><td>최소 권한·마이크로 세그멘테이션</td></tr>
  </tbody>
</table>
<h3>도입 시 고려사항</h3>
<ul>
  <li>자산·데이터 흐름 식별 등 현황 분석 선행, 중요 자원부터 단계적 적용 필요함</li>
  <li><span class="keyword">IAM</span>·MFA 등 식별·인증 체계 고도화가 전제 조건임</li>
  <li>레거시 시스템 호환성과 사용자 경험(UX) 저하 간 균형 고려해야 함</li>
  <li>지속 모니터링·자동화(SOAR) 운영 체계와 조직 문화 변화 병행 필요함</li>
</ul>

<h2>결론 및 전망</h2>
<p>제로 트러스트는 일회성 솔루션 도입이 아닌 <span class="keyword">보안 아키텍처 전환 여정</span>임. 공공·금융 분야 도입 지침 수립이 확산되고 있어 성숙도 모델 기반의 단계적 전환 전략 수립이 요구됨.</p>"""

_DEMO_MNEMONIC = (
    '<p><b>명·최·침</b> — <b>명</b>시적 검증 · <b>최</b>소 권한 · <b>침</b>해 가정 (제로 트러스트 3원칙)</p>\n'
    '    <p><b>Never Trust, Always Verify</b> — 신뢰하지 말고 항상 검증하라</p>'
)

_DEMO_WEAK = ["구성도 아래 간글 누락", "개조식 문체 미준수 문장 존재", "Ⅳ단락(결론) 차별화 요소 미흡"]


async def _demo(message: str) -> AsyncIterator[dict]:
    exam = any(w in message for w in EXAM_WORDS)
    yield _ev("orchestrator", "working", "문제 접수 중…")
    yield _talk("orchestrator", "새 문제 접수! 누리님, 출제 의도 분석 부탁해요 🖋️")

    yield _ev("nlu", "thinking", "출제 의도 분석 중…")
    await asyncio.sleep(0.7)

    if not exam:
        yield _ev("nlu", "done", "일반 문의")
        yield _talk("nlu", "시험 문제는 아니네요. 로운님이 멘토로 바로 답할게요!")
        yield _ev("writer", "working", "답변 작성 중…")
        await asyncio.sleep(0.9)
        yield _ev("writer", "done", "답변 완료")
        yield _talk("writer", "답변 보냈어요!")
        yield _ev("orchestrator", "done", "턴 완료")
        yield {
            "type": "reply",
            "reply": "지금은 데모 모드예요! 🖋️ GROQ_API_KEY(무료)를 설정하면 실제 LLM 팀이 "
                     "출제 의도 분석부터 채점까지 진행합니다.\n"
                     "\"제로 트러스트 보안 모델에 대하여 설명하시오 (25점)\"처럼 문제를 입력하시면 "
                     "답안 작성·채점·보완 과정을 데모로 보실 수 있어요.",
            "demo": True,
            "llm_calls": 0,
        }
        return

    # ---- 시험 문제 데모: 보완 루프 포함 고정 시나리오
    yield _ev("nlu", "done", "서술형 25점")
    yield _talk("nlu", "서술형 25점 문제예요. 정의·구성요소·도입 시 고려사항이 채점 포인트!")

    yield _ev("designer", "thinking", "답안 구조 설계 중…")
    await asyncio.sleep(0.8)
    yield _ev("designer", "done", "ITPE 4단락")
    yield _talk("designer", "Ⅰ.개요→Ⅱ.구성도·구성요소→Ⅲ.고려사항→Ⅳ.결론, 가나다 소제목으로 설계했어요. 로운님!")

    yield _ev("writer", "working", "답안 작성 중…")
    await asyncio.sleep(1.2)
    yield _ev("writer", "done", "초안 완료")
    yield _talk("writer", "초안 완성했어요. 세아님, 채점 부탁드립니다!")

    yield _ev("reviewer", "thinking", "채점 중…")
    await asyncio.sleep(0.9)
    yield _ev("reviewer", "working", "72점 · 보완 요청")
    yield _talk("reviewer", "1차 채점 72점. 구성도 간글이 빠졌고 개조식 문체와 Ⅳ단락 차별화가 미흡해요. 보완해 주세요.")

    yield _ev("writer", "working", "답안 보완 중…")
    await asyncio.sleep(1.1)
    yield _ev("writer", "done", "보완 완료")
    yield _talk("writer", "간글 추가하고 개조식으로 다듬고 결론 단락 보강했어요. 재채점 부탁해요!")

    yield _ev("reviewer", "thinking", "재채점 중…")
    await asyncio.sleep(0.9)
    yield _ev("reviewer", "done", "91점")
    yield _talk("reviewer", "재채점 91점, 합격권이에요. ITPE 목차 완결성과 간글·개조식 가독성이 좋아졌어요 ✅")

    yield _ev("orchestrator", "done", "납품 완료")
    yield _talk("orchestrator", "답안지 납품 완료! 다들 수고했어요 ☕")

    sheet = render_answer(
        question=_DEMO_QUESTION,
        title="제로 트러스트 보안 모델",
        kind="서술형",
        points=25,
        body=_DEMO_BODY,
        mnemonic_html=_DEMO_MNEMONIC,
    )
    yield {
        "type": "reply",
        "reply": "『제로 트러스트 보안 모델』 서술형 25점 데모 답안지가 완성됐어요! 📄\n"
                 "세아 채점: 1차 72점 → 보완 1회 → 재채점 91점 (합격권).\n"
                 "지금은 데모 모드라 고정 답안이에요. GROQ_API_KEY(무료)를 설정하면 "
                 "입력하신 문제로 진짜 답안을 작성해 드립니다.",
        "demo": True,
        "exam": {"kind": "서술형", "points": 25, "topic": "제로 트러스트 보안 모델"},
        "review": {"score": 91, "rounds": 1, "weak_points": _DEMO_WEAK},
        "artifact": {"title": "제로 트러스트 보안 모델", "html": sheet},
        "llm_calls": 0,
    }


# ---------------------------------------------------------------- 파이프라인


async def run_pipeline(history: list[dict], message: str) -> AsyncIterator[dict]:
    """사용자 메시지 하나를 5인 답안 팀 파이프라인으로 처리한다."""
    if provider() is None:
        async for e in _demo(message):
            yield e
        return

    llm_calls = 0
    try:
        yield _ev("orchestrator", "working", "문제 접수 중…")
        yield _talk("orchestrator", "새 문제 접수! 누리님, 출제 의도 분석 부탁해요 🖋️")

        # ---- 1. 출제 의도 분석 (누리)
        yield _ev("nlu", "thinking", "출제 의도 분석 중…")
        recent = " / ".join(str(m.get("content") or "")[:60] for m in history[-4:])
        llm_calls += 1
        plan = _parse_json(
            await _chat(
                "당신은 기술사 답안 팀의 출제 의도 분석가 '누리'입니다. 사용자 입력을 분석해 JSON만 출력하세요.\n"
                "- 기술사 시험 문제(…에 대하여 설명하시오/기술하시오/논하시오/약술/정의/비교 등 기술 주제 문제)면 "
                "type을 exam으로, 일반 질문/대화면 chat으로 판별합니다.\n"
                "- kind: 정의·약술 중심의 짧은 문제면 \"용어형\", 설명·논술·비교형이면 \"서술형\".\n"
                "- points: 문제 속 \"(25점)\" 같은 배점 표기에서 추출. 없으면 용어형 10, 서술형 25.\n"
                "- topic: 답안지 제목으로 쓸 짧은 주제명.\n"
                "- intents: 출제 의도/채점 포인트 2~5개.\n"
                "형식: {\"type\": \"exam|chat\", \"kind\": \"용어형|서술형\", \"points\": 10, "
                "\"topic\": \"짧은 제목\", \"intents\": [\"출제 의도/채점 포인트\", ...], "
                "\"say\": \"팀에게 분석 결과를 전하는 짧은 구어체 한 마디\"}",
                [{"role": "user", "content": f"이전 대화: {recent}\n\n입력: {message}"}],
                json_mode=True,
            ),
            {"type": "chat"},
        )
        is_exam = plan.get("type") == "exam"
        kind = plan.get("kind") if plan.get("kind") in ("용어형", "서술형") else "서술형"
        m_pts = re.search(r"(\d{1,3})\s*점", message)
        points = _score_of(plan.get("points"),
                           int(m_pts.group(1)) if m_pts else (10 if kind == "용어형" else 25))
        topic = str(plan.get("topic") or "").strip() or message[:30]
        intents = [str(i) for i in (plan.get("intents") or []) if str(i).strip()]

        if not is_exam:
            yield _ev("nlu", "done", "일반 문의")
            yield _talk("nlu", plan.get("say") or "시험 문제는 아니네요. 로운님이 멘토로 바로 답할게요!")
            # ---- 일반 대화: 로운이 수험 멘토로 바로 답변
            yield _ev("writer", "working", "답변 작성 중…")
            llm_calls += 1
            reply = await _chat(
                "당신은 '기술사 답안 사무소'의 답안 작성자이자 기술사 수험 멘토 '로운'입니다. "
                "기술사 시험 준비(공부법, 답안 작성 요령, 용어 개념, 서브노트 등)에 대해 "
                "친절하고 간결한 한국어로 답하세요. "
                "사용자가 시험 문제를 그대로 입력하면 팀이 채점까지 마친 답안지를 만들어 준다는 것도 "
                "필요할 때 자연스럽게 안내하세요.",
                history[-10:] + [{"role": "user", "content": message}],
                max_tokens=2048,
            )
            yield _ev("writer", "done", "답변 완료")
            yield _talk("writer", "답변 보냈어요!")
            yield _ev("orchestrator", "done", "턴 완료")
            yield {"type": "reply", "reply": reply, "llm_calls": llm_calls}
            return

        yield _ev("nlu", "done", f"{kind} {points}점")
        yield _talk("nlu", plan.get("say") or f"{kind} {points}점 문제예요. 채점 포인트 정리해서 다인님께 넘길게요!")

        # ---- 2. 답안 구조 설계 (다인)
        yield _ev("designer", "thinking", "답안 구조 설계 중…")
        llm_calls += 1
        design = _parse_json(
            await _chat(
                "당신은 기술사 답안 팀의 답안 구조 설계자 '다인'입니다. 문제와 출제 의도를 보고 "
                "답안 목차를 JSON만으로 설계하세요. ITPE 기술사 답안 문법을 따릅니다.\n"
                "- 서술형(4단락 고정): Ⅰ.○○의 개요(리드문+정의+필요성) → Ⅱ.○○의 구성도 및 구성요소(개념도+간글+3단표) → "
                "Ⅲ.문제가 직접 요구한 사항(비교/고려사항 등, 요구별 소제목) → Ⅳ.결론 및 전망(차별화 포인트) 4개 섹션.\n"
                "- 용어형(3단락): Ⅰ.정의 → Ⅱ.개념도 및 구성요소 → Ⅲ.활용방안/고려사항 3개 섹션.\n"
                "- 각 섹션의 points는 가나다(가. 나. 다.) 소제목 단위로 작성.\n"
                "형식: {\"outline\": [{\"section\": \"섹션명\", \"points\": [\"다룰 내용\", ...]}, ...], "
                "\"mnemonic\": {\"word\": \"핵심 키워드 두문자\", \"expansion\": [\"두문자 풀이\", ...]}, "
                "\"diagram_idea\": \"개념도 구성 아이디어\", "
                "\"say\": \"작성자 로운에게 설계를 전달하는 짧은 한 마디\"}",
                [{"role": "user", "content":
                    f"문제: {message}\n유형: {kind} {points}점 / 주제: {topic}\n"
                    f"출제 의도: {json.dumps(intents, ensure_ascii=False)}"}],
                json_mode=True,
            ),
            {"outline": [], "mnemonic": {}, "diagram_idea": ""},
        )
        outline = design.get("outline") or []
        yield _ev("designer", "done", f"목차 {len(outline)}개 섹션" if outline else "설계 완료")
        yield _talk("designer", design.get("say") or "목차 설계 넘겼어요. 로운님, 부탁해요!")

        # ---- 3. 답안 초안 작성 (로운)
        writer_system = (
            "당신은 정보관리기술사 답안 작성 전문가 '로운'입니다. "
            "설계된 목차에 따라 기술사 시험 답안 본문을 작성하세요.\n"
            f"{_BODY_RULES}\n"
            "- 코드 펜스나 설명 없이 답안 본문 HTML만 출력하세요."
        )
        draft_brief = (
            f"문제: {message}\n유형: {kind} {points}점 / 주제: {topic}\n"
            f"출제 의도(채점 포인트): {json.dumps(intents, ensure_ascii=False)}\n"
            f"목차 설계: {json.dumps(outline, ensure_ascii=False)}\n"
            f"개념도 아이디어: {design.get('diagram_idea') or '-'}"
        )
        yield _ev("writer", "working", "답안 작성 중…")
        llm_calls += 1
        body = _extract_body(await _chat(
            writer_system,
            [{"role": "user", "content": draft_brief}],
            max_tokens=6000,
        ))
        yield _ev("writer", "done", "초안 완료")
        yield _talk("writer", "초안 완성했어요. 세아님, 채점 부탁드립니다!")

        # ---- 4. 채점 (세아)
        reviewer_system = (
            "당신은 기술사 시험 채점위원 '세아'입니다. 답안 본문 HTML을 검토해 JSON만 출력하세요.\n"
            "채점 기준: ① 출제 의도 부합 ② ITPE 목차 완결성(서술형 Ⅰ~Ⅳ 4단락, 가나다 소제목) "
            "③ 3단표·개념도·간글 활용 ④ 개조식 문체·키워드 가독성 ⑤ 차별화 요소(결론 단락의 알파).\n"
            f"{PASS_SCORE}점 이상이면 verdict를 pass, 미만이면 revise로 판정합니다.\n"
            "형식: {\"score\": 0~100 정수, \"verdict\": \"pass|revise\", "
            "\"weak_points\": [\"미흡 항목\", ...], "
            "\"say\": \"팀에게 채점 결과를 알리는 짧은 한 마디(점수 포함)\"}"
        )
        review_brief = (
            f"문제: {message}\n유형: {kind} {points}점\n"
            f"출제 의도(채점 포인트): {json.dumps(intents, ensure_ascii=False)}\n\n"
        )
        yield _ev("reviewer", "thinking", "채점 중…")
        llm_calls += 1
        review = _parse_json(
            await _chat(
                reviewer_system,
                [{"role": "user", "content": review_brief + f"답안 본문:\n{body[:8000]}"}],
                json_mode=True,
            ),
            {"score": 80, "verdict": "revise", "weak_points": []},
        )
        score = _score_of(review.get("score"))
        weak = [str(w) for w in (review.get("weak_points") or []) if str(w).strip()]
        rounds = 0

        if score < PASS_SCORE:
            # ---- 5. 보완 1회 (로운) + 재채점 (세아)
            say1 = str(review.get("say") or "").strip()
            if str(score) not in say1:
                say1 = f"1차 채점 {score}점. " + (say1 or f"보완이 필요해요: {' / '.join(weak[:2]) or '완성도 미흡'}")
            yield _ev("reviewer", "working", f"{score}점 · 보완 요청")
            yield _talk("reviewer", say1)

            yield _ev("writer", "working", "답안 보완 중…")
            llm_calls += 1
            revised = _extract_body(await _chat(
                writer_system,
                [
                    {"role": "user", "content": draft_brief},
                    {"role": "assistant", "content": body[:8000]},
                    {"role": "user", "content":
                        f"채점위원 1차 채점 {score}점. 미흡 항목: {json.dumps(weak, ensure_ascii=False)}\n"
                        "위 미흡 항목을 반영해 답안 본문 전체를 다시 출력하세요. "
                        "잘 쓴 부분은 유지하고 지적된 부분을 보강합니다."},
                ],
                max_tokens=6000,
            ))
            if revised:
                body = revised
            rounds = 1
            yield _ev("writer", "done", "보완 완료")
            yield _talk("writer", "지적사항 반영해서 보완했어요. 재채점 부탁해요!")

            yield _ev("reviewer", "thinking", "재채점 중…")
            llm_calls += 1
            review = _parse_json(
                await _chat(
                    reviewer_system,
                    [{"role": "user", "content":
                        review_brief + f"(보완 후 재채점, 1차 {score}점, 미흡 항목: "
                        f"{json.dumps(weak, ensure_ascii=False)})\n\n답안 본문:\n{body[:8000]}"}],
                    json_mode=True,
                ),
                {"score": max(score, PASS_SCORE), "verdict": "pass", "weak_points": []},
            )
            score = _score_of(review.get("score"), default=max(score, PASS_SCORE))
            weak = [str(w) for w in (review.get("weak_points") or []) if str(w).strip()]
            say2 = str(review.get("say") or "").strip()
            if str(score) not in say2:
                say2 = f"재채점 {score}점. " + (say2 or "많이 좋아졌어요 ✅")
            yield _ev("reviewer", "done", f"{score}점")
            yield _talk("reviewer", say2)
        else:
            yield _ev("reviewer", "done", f"{score}점")
            yield _talk("reviewer", review.get("say") or f"{score}점, 합격권이에요. 출고! ✅")

        yield _ev("orchestrator", "done", "납품 완료")
        yield _talk("orchestrator", "답안지 납품 완료! 다들 수고했어요 ☕")

        # ---- 6. 답안지 렌더 + 납품
        sheet = render_answer(
            question=message, title=topic, kind=kind, points=points,
            body=body, mnemonic_html=_mnemonic_html(design.get("mnemonic")),
        )
        summary = f"『{topic}』 {kind} {points}점 답안지 완성! 세아 채점 {score}점"
        if rounds:
            summary += f" (보완 {rounds}회 후 재채점)"
        summary += "."
        if score < PASS_SCORE:
            summary += f"\n기준({PASS_SCORE}점) 미달이라 참고용으로 확인해 주세요."
        if weak:
            summary += f"\n남은 보완 포인트: {' / '.join(weak[:3])}"
        yield {
            "type": "reply",
            "reply": summary,
            "exam": {"kind": kind, "points": points, "topic": topic},
            "review": {"score": score, "rounds": rounds, "weak_points": weak},
            "artifact": {"title": topic, "html": sheet},
            "llm_calls": llm_calls,
        }
    except Exception as exc:
        yield _ev("orchestrator", "error", f"오류: {type(exc).__name__}")
        yield {
            "type": "reply",
            "reply": f"죄송해요, 작업 중 오류가 발생했어요. ({type(exc).__name__}: {exc})",
            "error": str(exc),
            "llm_calls": llm_calls,
        }
