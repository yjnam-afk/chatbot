"""기술사 답안 사무소 — 멀티 에이전트 답안 작성 파이프라인 (Vercel 서버리스 + 무료 LLM 대응).

5인 팀이 기술사 시험 답안지를 만든다 (역할 = 라이브러리 파이프라인 코드 단계와 1:1):
접수(코디, 0콜) → 토픽 검색(누리, 0~1콜) → 답안 편집(다인, 0콜) → 집필(로운, 0~n콜) → 검증(세아, 0~1콜)

- 시험 문제 + 라이브러리 적중: 부품 조립 — LLM 0~2콜, 키 없이도 실답안 (3초 목표)
- 시험 문제 + 미적중: 라이브 파이프라인 폴백 — 설계→초안→채점(85점 미만/형식 위반 시
  보완 1회+재채점), 최대 5콜. 키 없으면 데모 시나리오
- 일반 질문: 로운(수험 멘토)이 바로 답변 (1콜)

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

import _topic_library as _library
from _assembler import assemble, short_name, split_subjects

# 역할은 라이브러리 파이프라인의 실제 코드 단계와 1:1 (docs/library-spec.md 2-5).
# 프런트 표기의 단일 출처 — progress.js는 role 문자열을 하드코딩하지 않는다.
AGENTS = [
    {"id": "orchestrator", "name": "코디", "role": "접수", "color": "#f2b544"},
    {"id": "nlu", "name": "누리", "role": "토픽 검색", "color": "#5bc8f5"},
    {"id": "designer", "name": "다인", "role": "답안 편집", "color": "#b78ef0"},
    {"id": "writer", "name": "로운", "role": "집필", "color": "#6fd88a"},
    {"id": "reviewer", "name": "세아", "role": "검증", "color": "#f28ba8"},
]

PASS_SCORE = 85


def provider() -> dict | None:
    """설정된 환경변수로 LLM 공급자를 판별한다. 없으면 None(데모 모드).

    우선순위: custom > gemini > groq.
    답안 생성은 토큰 사용량이 커서(호출 4~6회 + 긴 출력) 무료 TPM(분당 토큰)이
    큰 Gemini를 우선한다. Groq 무료 티어는 TPM이 작아 파이프라인 중 429 가능.
    """
    if os.environ.get("LLM_API_KEY") and os.environ.get("LLM_BASE_URL"):
        return {
            "name": "custom",
            "key": os.environ["LLM_API_KEY"],
            "base": os.environ["LLM_BASE_URL"],
            "model": os.environ.get("LLM_MODEL", ""),
        }
    if os.environ.get("GEMINI_API_KEY"):
        return {
            "name": "gemini",
            "key": os.environ["GEMINI_API_KEY"],
            "base": "https://generativelanguage.googleapis.com/v1beta/openai",
            "model": os.environ.get("LLM_MODEL", "gemini-2.5-flash"),
        }
    if os.environ.get("GROQ_API_KEY"):
        return {
            "name": "groq",
            "key": os.environ["GROQ_API_KEY"],
            "base": "https://api.groq.com/openai/v1",
            "model": os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile"),
        }
    return None


class RateLimitError(Exception):
    """무료 LLM 분당 토큰(TPM) 한도 초과 — 재시도 후에도 429일 때 사용자 안내용."""


def _retry_wait(retry_after: str | None) -> float:
    """429 응답의 Retry-After를 대기 시간(초)으로. 없거나 파싱 불가면 5초,
    Vercel 함수 60초 예산을 고려해 최대 15초로 캡."""
    try:
        wait = float(retry_after) if retry_after else 5.0
    except (TypeError, ValueError):
        wait = 5.0
    return min(max(wait, 0.0), 15.0)


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
    url = f"{p['base'].rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {p['key']}"}
    async with httpx.AsyncClient(timeout=55) as client:
        r = await client.post(url, headers=headers, json=payload)
        if r.status_code == 429:
            # 무료 티어 TPM 한도 — Retry-After만큼 대기 후 1회만 재시도
            await asyncio.sleep(_retry_wait(r.headers.get("retry-after")))
            r = await client.post(url, headers=headers, json=payload)
            if r.status_code == 429:
                raise RateLimitError()
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
    """LLM 출력에서 답안 본문 HTML 프래그먼트를 추출한다 (```펜스/서두 잡담/문서 꼬리 제거)."""
    m = re.search(r"```(?:html)?\s*(.*?)```", text or "", re.S)
    if m:
        text = m.group(1)
    text = (text or "").strip()
    starts = [i for i in (text.find('<p class="ans"'), text.find("<h2")) if i >= 0]
    if starts and min(starts) > 0:
        text = text[min(starts):]
    text = re.sub(r"</(?:main|body|html)\s*>.*$", "", text, flags=re.S | re.I)
    return text.strip()


# ---- 줄 그리드 분량 모델 (docs/answer-template-spec.md §1)
_TAIL_LINES = 5    # 템플릿이 body 뒤에 붙이는 꼬리: "끝" 1 + 여백 1 + 두문자 박스 3
_PAGE1_BODY = 17   # 1쪽 본문 줄 수 (머리행 1 + 문제 스트립 4 제외)
_PAGEN_BODY = 21   # 2쪽부터 본문 줄 수 (머리행 1 제외)
_PAGE_LINES = 22   # 답안지 1매 환산 기준 (머리행 포함)


def _count_lines(body_html: str, p2: bool) -> int:
    """본문 프래그먼트의 점유 줄 수를 요소별 규칙(스펙 §2)으로 합산한다."""
    b = body_html or ""
    lines = 0
    lines += 7 * len(re.findall(r'class="diagram d7"', b))
    lines += 6 * len(re.findall(r'class="diagram"', b))
    lines += len(re.findall(r"<tr[\s>]", b, re.I))            # 표 행 1줄
    lines += len(re.findall(r'<tr\s+class="r2"', b, re.I))    # r2 행은 +1줄
    n_h2 = len(re.findall(r"<h2[\s>]", b, re.I))
    lines += n_h2 + len(re.findall(r"<h3[\s>]", b, re.I))
    n_def = len(re.findall(r'<p\s+class="def"', b, re.I))
    n_p = len(re.findall(r"<p[\s>]", b, re.I))
    lines += n_def * 2 + max(0, n_p - n_def)                  # def 2줄, 그 외 p 1줄
    lines += len(re.findall(r'<div\s+class="gap"', b, re.I))
    if p2 and n_h2 > 1:
        lines += n_h2 - 1   # 2교시형 단락 사이 자동 1줄 (.p2 h2 margin-top)
    return lines


def _volume(body_html: str, kind: str) -> dict:
    """답안 분량 — 줄 그리드 기반. 쪽/마지막 쪽 줄/매 환산(총줄÷22)을 산출한다."""
    is_terms = "1교시" in str(kind)
    total = _count_lines(body_html, p2=not is_terms) + _TAIL_LINES
    if total <= _PAGE1_BODY:
        pages, line_in_page = 1, total
    else:
        extra = -(-(total - _PAGE1_BODY) // _PAGEN_BODY)  # ceil
        pages = 1 + extra
        line_in_page = (total - _PAGE1_BODY) - _PAGEN_BODY * (extra - 1)
    return {
        "lines": total,
        "pages": pages,
        "line_in_page": line_in_page,
        "pages_frac": round(total / _PAGE_LINES, 1),
        "target_pages": 1.4 if is_terms else 3.5,
    }


def _lint_format(body_html: str, kind: str) -> list[str]:
    """서버 측 형식 린터 — LLM이 프롬프트 규칙을 무시해도 코드로 강제 검사한다.

    위반 지적 문자열 목록을 반환 (빈 목록 = 통과). LLM 호출 없음.
    검사 기준: 줄 그리드 계약(docs/answer-template-spec.md §7).
    """
    issues: list[str] = []
    body = body_html or ""
    is_terms = "1교시" in kind
    # 1) 단락(h2) 수: 2교시형 Ⅰ~Ⅳ 4개, 1교시형 Ⅰ~Ⅲ 3개
    need_h2 = 3 if is_terms else 4
    n_h2 = len(re.findall(r"<h2[\s>]", body, re.I))
    if n_h2 != need_h2:
        issues.append(f"단락 수 {n_h2}개(기준 {need_h2}개) — "
                      f"{'Ⅰ~Ⅲ' if is_terms else 'Ⅰ~Ⅳ'} 구조로 재편 필요")
    # 2) "답)" 표기
    if 'class="ans"' not in body:
        issues.append("\"답)\" 표기 누락 — 첫 줄 <p class=\"ans\">답)</p>")
    # 3) 개념도
    if not re.search(r"<div[^>]*class=\"[^\"]*diagram", body, re.I):
        issues.append("개념도 누락 — div.diagram 1개 이상 필요")
    # 4) 표 개수 (구성요소 상세표 + 결론/비교표)
    n_table = len(re.findall(r"<table[\s>]", body, re.I))
    if n_table < 2:
        issues.append(f"표 부족(현재 {n_table}개, 기준 2개 이상) — "
                      "구성요소 3단표·기대효과/비교표 필요")
    # 5) 허용 밖 <p>: class가 ans/def/gloss 가 아닌 문단 금지
    free_p = sum(1 for m in re.finditer(r"<p(\s[^>]*)?>", body, re.I)
                 if not re.search(r'class="(?:ans|def|gloss)"', m.group(1) or ""))
    if free_p:
        issues.append(f"허용 밖 문단 {free_p}개 — p는 class ans/def/gloss만 허용")
    n_list = len(re.findall(r"<[uo]l[\s>]", body, re.I))
    if n_list:
        issues.append(f"목록(ul/ol) {n_list}개 — 표로 전환 필요")
    # 6) 개조식 약식 검사: "합니다/입니다" 종결 빈도
    text = re.sub(r"<[^>]+>", " ", body)
    long_style = len(re.findall(r"(?:합니다|입니다)", text))
    if long_style >= 3:
        issues.append(f"만연체 {long_style}회 — 개조식(~임/~함/~됨) 종결 필요")
    return issues


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
/* 기술사 답안지 — 실물 줄 그리드 템플릿 (docs/answer-template-spec.md, v1 연속 본문 방식)
   모든 요소가 1줄(--lh)의 정수배로 괘선에 스냅된다. */
* { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --lh: 32px;
  --ink: #1c2f4a; --chrome: #3d4148;
  --rule: #c5cedd; --rule2: #9db0c8; --paper: #fdfdfa;
}
@counter-style ganada {
  system: fixed; symbols: "가" "나" "다" "라" "마" "바" "사" "아" "자" "차";
  suffix: ". ";
}
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  background: #e6e5e0; color: var(--ink);
  font-family: "Noto Serif KR", "Noto Serif CJK KR", "Nanum Myeongjo", "Source Han Serif K", Batang, AppleMyungjo, serif;
  font-size: 15px; padding: 36px 12px 48px; word-break: keep-all;
}
.page {
  width: min(794px, 100%); margin: 0 auto;
  background: var(--paper); border: 1px solid #c9c5ba;
  box-shadow: 0 2px 18px rgba(40, 40, 30, 0.12);
}
.page-head {
  height: var(--lh); display: flex; align-items: center;
  font-family: system-ui, sans-serif; font-size: 11.5px; color: var(--chrome);
  border-bottom: 2px solid var(--rule2);
}
.ph-box { width: 88px; height: 100%; display: flex; align-items: center; justify-content: center; border-right: 1px solid var(--rule2); letter-spacing: 0.3em; }
.ph-title { flex: 1; text-align: center; letter-spacing: 0.2em; }
.ph-num { width: 88px; text-align: center; border-left: 1px solid var(--rule2); }
.q-strip {
  height: calc(4 * var(--lh)); padding: 8px 20px 0;
  border-bottom: 2px solid var(--rule2);
  font-family: system-ui, sans-serif; color: var(--chrome); overflow: hidden;
}
.qs-top { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; }
.qs-no { font-weight: 700; font-size: 14px; }
.qs-kind { font-size: 11.5px; font-weight: 700; border: 1px solid var(--chrome); padding: 1px 10px; border-radius: 2px; white-space: nowrap; }
.qs-text { margin-top: 6px; font-size: 14px; line-height: 1.65; }
.body {
  position: relative;
  min-height: calc(var(--body, 17) * var(--lh));
  background: repeating-linear-gradient(to bottom,
    transparent 0 calc(var(--lh) - 1px),
    var(--rule) calc(var(--lh) - 1px) var(--lh));
}
.body::before { content: ""; position: absolute; left: 44px; top: 0; bottom: 0; width: 1px; background: var(--rule2); }
.content { margin: 0 20px 0 58px; }
.content h2, .content h3, .content p { line-height: var(--lh); font-size: 15px; font-weight: 400; }
.content h2 { font-weight: 700; counter-increment: sec; counter-reset: sub; }
.content h2::before { content: counter(sec, upper-roman) ". "; }
.content h3 { font-weight: 700; counter-increment: sub; padding-left: 18px; }
.content h3::before { content: counter(sub, ganada) ". "; }
/* 2교시형: 단락(h2) 사이 1줄 띄움 — 1교시형(.p1)은 띄우지 않는다 */
.content.p2 h2:not(:first-of-type) { margin-top: var(--lh); }
.ans { font-weight: 700; }
.def { min-height: calc(2 * var(--lh)); padding-left: 18px; }
.gloss { padding-left: 18px; }
.end { text-align: right; padding-right: 12px; font-weight: 700; }
.gap { height: var(--lh); }
.content u, .content .keyword {
  text-decoration: underline; text-underline-offset: 5px; text-decoration-thickness: 1px;
  font-weight: inherit; border: none;
}
.content table { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 13.5px; }
.content th, .content td { border: 1px solid var(--ink); padding: 2px 8px; vertical-align: middle; line-height: 1.5; overflow: hidden; }
.content th { font-weight: 700; text-align: center; background: rgba(28, 47, 74, 0.04); }
.content tr { height: var(--lh); }
.content tr.r2 { height: calc(2 * var(--lh)); }
.t3 th:nth-child(1) { width: 20%; }
.t3 th:nth-child(2) { width: 20%; }
.t2 th:nth-child(1), .t2 td:first-child { width: 20%; }
.tcmp th:nth-child(1) { width: 20%; }
.tcmp th:nth-child(2) { width: 40%; }
.texp th:nth-child(1) { width: 20%; }
.texp th:nth-child(2) { width: 19%; }
.diagram {
  height: calc(6 * var(--lh)); border: 1.5px solid var(--ink);
  display: flex; align-items: center; justify-content: center;
  gap: 22px; padding: 0 12px; overflow: hidden;
}
.diagram.d7 { height: calc(7 * var(--lh)); }
.d-circle {
  width: 60px; height: 60px; border-radius: 50%;
  border: 1.5px solid var(--ink); background: #fff;
  display: flex; align-items: center; justify-content: center;
  text-align: center; font-size: 10.5px; font-weight: 400; line-height: 1.25; padding: 4px;
}
.d-sep { align-self: stretch; border-left: 1px dashed var(--ink); margin: 10px 0; }
.d-col { display: flex; flex-direction: column; align-items: center; gap: 16px; }
.d-row { display: flex; align-items: center; gap: 14px; }
.d-box { border: 1.5px solid var(--ink); background: #fff; padding: 4px 14px; text-align: center; font-size: 13px; font-weight: 700; line-height: 1.4; }
.d-box small { display: block; font-size: 11px; font-weight: 400; }
.d-box.soft { border-style: dashed; border-width: 1px; font-weight: 400; }
.d-box.d-hub { border-width: 2.5px; padding: 12px 20px; font-size: 15px; }
.d-arrow { font-weight: 700; font-size: 17px; flex: none; }
.mnemonic { height: calc(3 * var(--lh)); border: 1.5px dashed var(--ink); padding: 0 14px; overflow: hidden; }
.mn-label { line-height: var(--lh); font-size: 12px; font-weight: 700; letter-spacing: 0.25em; }
.mnemonic p { line-height: var(--lh); font-size: 14px; }
.mnemonic b { border-bottom: 1px solid var(--ink); }
@page { size: A4 portrait; margin: 12mm 14mm; }
@media print {
  :root { --lh: 10.5mm; }
  body { background: #fff; padding: 0; }
  .page { width: auto; margin: 0; border: none; box-shadow: none; }
  .content h2, .content h3 { break-after: avoid; page-break-after: avoid; }
  .diagram, .mnemonic, tr { break-inside: avoid; page-break-inside: avoid; }
}
</style>
</head>
<body>
<div class="page">
  <div class="page-head">
    <span class="ph-box">번 호</span>
    <span class="ph-title">기 술 사 답 안 지 ({ptitle})</span>
    <span class="ph-num">1 쪽</span>
  </div>
  <div class="q-strip">
    <div class="qs-top">
      <span class="qs-no">문) {title}</span>
      <span class="qs-kind">{kind} · {points}점</span>
    </div>
    <p class="qs-text">{question}</p>
  </div>
  <div class="body" style="--body:{lines}">
    <div class="content {pcls}">
{body}
      <p class="end">"끝"</p>
      <div class="gap"></div>
      <div class="mnemonic">
        <div class="mn-label">두문자 암기 포인트</div>
        {mnemonic_html}
      </div>
    </div>
  </div>
</div>
</body>
</html>"""


def render_answer(question: str, title: str, kind: str, points: int | str,
                  body: str, mnemonic_html: str) -> str:
    """답안지 템플릿에 내용을 채워 완성 HTML을 만든다.

    CSS 중괄호 때문에 str.format() 금지. 입력값에 "{body}" 같은 리터럴
    플레이스홀더가 있어도 재치환되지 않도록 단일 패스 re.sub로 치환한다.
    question/title/kind/points는 escape, body/mnemonic_html은 이미 HTML.
    kind에 따라 content 래퍼(p1|p2)와 머리행 표기를 정하고, 본문 줄 수를
    페이지 경계(1쪽 17줄 + n×21줄)로 올림해 마지막 쪽 끝까지 괘선을 채운다.
    """
    is_terms = "1교시" in str(kind)
    total = _count_lines(body, p2=not is_terms) + _TAIL_LINES
    if total <= _PAGE1_BODY:
        padded = _PAGE1_BODY
    else:
        padded = _PAGE1_BODY + _PAGEN_BODY * (-(-(total - _PAGE1_BODY) // _PAGEN_BODY))
    parts = {
        "title": html_mod.escape(str(title)),
        "kind": html_mod.escape(str(kind)),
        "points": html_mod.escape(str(points)),
        "question": html_mod.escape(str(question)),
        "body": body,
        "mnemonic_html": mnemonic_html,
        "pcls": "p1" if is_terms else "p2",
        "ptitle": "제 1 교 시 형" if is_terms else "제 2 교 시 형",
        "lines": str(padded),
    }
    return re.sub(r"\{(title|kind|points|question|body|mnemonic_html|pcls|ptitle|lines)\}",
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

_BODY_RULES = """[답안 본문 HTML 규칙 — 실물 답안지 줄 그리드 계약 (docs/answer-template-spec.md §7)]
- 1쪽 괘선 안 내용만 HTML 프래그먼트로 출력. 문제 스트립·머리행·"끝"·두문자 박스는 서버가 붙임(직접 쓰지 말 것).
- 허용 태그·클래스 (이 목록 밖 금지):
  h2                  단락 제목 — 로마 숫자 자동, "I." 직접 쓰지 말 것
  h3                  하부 제목 — 가나다 자동, "가." 직접 쓰지 말 것
  p class="ans"       첫 줄 "답)" 1회
  p class="def"       2줄 문단(정의·특징·마무리 설명), 공백 포함 70자 이내
  p class="gloss"     간글 1줄, "– "로 시작, 40자 이내
  u                   키워드 밑줄(섹션당 1~3개), 강조 인용은 "쌍따옴표" 텍스트
  table class="t3|t2|tcmp|texp" + thead/tbody/tr/th/td — 2줄 행은 <tr class="r2">
  div class="diagram"     개념도 6줄 컨테이너 (답안 전체 1~2개, 일도일표)
  div class="diagram d7"  2교시 서론 로드맵 전용 7줄 컨테이너 (서론에 1개만)
    내부 전용: div.d-row / div.d-col / div.d-box(+.soft 점선 보조, +.d-hub 중심 강조) /
               div.d-circle(등장배경 원형) / span.d-sep(구분 점선) / span.d-arrow(→ ← ↓ ↔ 텍스트)
- 표 종류: t3 = 3단표(구분 20/구성요소 20/설명 60) / t2 = 2단표(구분 20/설명 80) /
  tcmp = 비교표(구분 20/40/40) / texp = 2가지 설명표(20/19/61)
- 문체: 개조식("~임/~함/~됨" 종결). 정의·설명은 키워드 나열형 — 문장을 만들지 말 것.
- 표 셀은 1줄 15자, 2줄 행(r2) 셀은 34자 이내. h2 사이에 빈 줄·gap을 직접 넣지 말 것(2교시형은 CSS 자동).

[1교시형(용어, 10점) — 3단락, 26~30줄]
<p class="ans">답)</p>
I. 리드문형 제목 "○○를 위한 △△의 개요" (h2) → 가. 정의 (h3 + p.def 키워드 나열형) → 나. 특징/목적 (h3 + p.def)
II. 개념도·구성요소 (h2) → 가. 개념도 (h3 + div.diagram + p.gloss) → 나. 구성요소 (h3 + table.t3 헤더1+행 4~6)
III. 활용/비교/결론 (h2 + table 또는 p.def) — 수직 확장·인접 기술 비교로 차별화

[2교시형(서술, 25점) — 4단락, 66~77줄]  ※ 3·4교시 문제도 동일 취급
I. 서론 0.5쪽: 리드문형 제목(h2) + 로드맵 그림(div.diagram.d7 — 물어본 항목들을 하나의 그림으로,
   왼쪽 등장배경 d-circle 3개 + d-sep 구분, 가운데 d-hub, 오른쪽 기대효과) + (정의) p.def + (필요성) p.def
II. 본론1 1쪽: 제목(h2) + 가. 구성도 (h3 + div.diagram + p.gloss) + 나. 구성요소
   (h3 + table.t3 = 헤더 1행 + <tr class="r2"> 4행, 총 9줄) + 마무리 p.def
III. 본론2 1쪽: 문제가 물어본 요구사항을 지문 문구 그대로 제목·헤더로 (h2 + h3/표 중심 — 승부처, 깊이 있게)
IV. 결론·알파 0.5쪽: 제목(h2) + 기대효과 table.t2 3~4행 + 결론 p.def

- 개념도 예시 (6줄 컨테이너, flex 배치):
<div class="diagram"><div class="d-col"><div class="d-box">평가·인증<small>eSCM · ISO 20000</small></div><div class="d-box">서비스수준<small>SOW · SLA</small></div></div><span class="d-arrow">→</span><div class="d-box d-hub">ITSM<small>고품질 IT 서비스 관리체계</small></div><span class="d-arrow">←</span><div class="d-col"><div class="d-box">품질인증<small>CMMI</small></div><div class="d-box">Best Practice<small>ITIL</small></div></div></div>"""


# ---------------------------------------------------------------- 데모 모드

_DEMO_QUESTION = ("제로 트러스트 보안 모델의 개념, 구성요소, "
                  "도입 시 고려사항에 대하여 설명하시오 (25점)")

_DEMO_BODY = """<p class="ans">답)</p>
<h2>경계 없는 보안을 위한 제로 트러스트의 개요</h2>
<div class="diagram d7">
  <div class="d-col">
    <div class="d-circle">경계<br>소멸</div>
    <div class="d-circle">내부자<br>위협</div>
    <div class="d-circle">클라우드<br>확산</div>
  </div>
  <span class="d-sep"></span>
  <span class="d-arrow">→</span>
  <div class="d-col">
    <div class="d-box d-hub">제로 트러스트 도입</div>
    <div class="d-box soft">구성요소 (Ⅱ)</div>
    <div class="d-box soft">도입 시 고려사항 (Ⅲ)</div>
  </div>
  <span class="d-arrow">→</span>
  <span class="d-sep"></span>
  <div class="d-box">전 자원 상시 검증<small>Never Trust, Always Verify</small></div>
</div>
<p class="def">(정의) 내·외부 구분 없이 모든 접근을 <u>상시 검증</u>하는 "Never Trust, Always Verify" 기반 보안 모델</p>
<p class="def">(필요성) 경계 방어 한계 극복, <u>측면 이동 차단</u>, 클라우드·원격근무 환경의 자원 단위 보호</p>
<h2>제로 트러스트의 구성도 및 구성요소</h2>
<h3>제로 트러스트의 구성도</h3>
<div class="diagram">
  <div class="d-col">
    <div class="d-box">주체<small>사용자 · 기기</small></div>
    <div class="d-box soft">신뢰도 평가<small>ID · 기기상태 · 위협정보</small></div>
  </div>
  <span class="d-arrow">→</span>
  <div class="d-box d-hub">PDP<small>정책 결정 지점</small></div>
  <span class="d-arrow">→</span>
  <div class="d-col">
    <div class="d-box">PEP<small>정책 시행 지점</small></div>
    <div class="d-box soft">보호 자원<small>데이터 · 시스템</small></div>
  </div>
</div>
<p class="gloss">– PDP의 동적 판단과 PEP의 세션 통제로 자원 단위 보호</p>
<h3>제로 트러스트의 구성요소</h3>
<table class="t3">
  <thead><tr><th>구분</th><th>구성요소</th><th>설명</th></tr></thead>
  <tbody>
    <tr class="r2"><td>제어</td><td>PDP<br>정책 엔진</td><td>가용 신호 기반으로 접근 허용 여부를 동적 결정</td></tr>
    <tr class="r2"><td>시행</td><td>PEP</td><td>결정된 정책에 따라 세션 생성·유지·차단 시행</td></tr>
    <tr class="r2"><td>평가</td><td>신뢰도 입력</td><td>ID·기기 상태·위협 인텔리전스 지속 평가</td></tr>
    <tr class="r2"><td>격리</td><td>마이크로<br>세그멘테이션</td><td>자원 단위 분할로 측면 이동 차단</td></tr>
  </tbody>
</table>
<p class="def">"명시적 검증 · 최소 권한 · 침해 가정"의 3원칙을 구현하는 접근 제어 체계임</p>
<h2>제로 트러스트 도입 시 고려사항</h2>
<h3>기존 경계 모델과의 비교</h3>
<table class="tcmp">
  <thead><tr><th>구분</th><th>경계 보안</th><th>제로 트러스트</th></tr></thead>
  <tbody>
    <tr><td>신뢰 기준</td><td>내부망 암묵 신뢰</td><td>위치 무관 상시 검증</td></tr>
    <tr><td>방어 지점</td><td>네트워크 경계</td><td>자원 단위(ID·데이터)</td></tr>
    <tr><td>검증 시점</td><td>최초 접속 1회</td><td>세션 전체 지속 인증</td></tr>
  </tbody>
</table>
<h3>단계적 도입 고려사항</h3>
<table class="t2">
  <thead><tr><th>구분</th><th>고려사항</th></tr></thead>
  <tbody>
    <tr><td>전략</td><td>자산·데이터 흐름 식별 후 중요 자원부터 단계 적용</td></tr>
    <tr><td>기술</td><td><u>IAM</u>·MFA 등 식별·인증 체계 고도화 선행</td></tr>
    <tr><td>운영</td><td>레거시 호환·UX 저하 균형, 상시 모니터링 병행</td></tr>
  </tbody>
</table>
<h2>도입 기대효과 및 결론</h2>
<table class="t2">
  <thead><tr><th>기대효과</th><th>설명</th></tr></thead>
  <tbody>
    <tr><td>피해 최소화</td><td>침해 가정 설계로 확산 범위 국소화</td></tr>
    <tr><td>가시성 확보</td><td>전 접근 로깅으로 위협 탐지력 향상</td></tr>
  </tbody>
</table>
<p class="def">제로 트러스트는 일회성 도입이 아닌 <u>보안 아키텍처 전환 여정</u>으로, 성숙도 기반 단계 전환이 요구됨</p>"""

_DEMO_MNEMONIC = (
    '<p><b>명·최·침</b> — <b>명</b>시적 검증 · <b>최</b>소 권한 · <b>침</b>해 가정 (제로 트러스트 3원칙)</p>\n'
    '    <p><b>Never Trust, Always Verify</b> — 신뢰하지 말고 항상 검증하라</p>'
)

_DEMO_WEAK = ["구성도 아래 간글 누락", "개조식 문체 미준수 문장 존재", "Ⅳ단락(결론) 차별화 요소 미흡"]


async def _demo_chat() -> AsyncIterator[dict]:
    """일반 질문 데모 (키 없음)."""
    yield _ev("writer", "working", "답변 작성 중…")
    await asyncio.sleep(0.6)
    yield _ev("writer", "done", "답변 완료")
    yield _talk("writer", "답변 보냈어요!")
    yield _ev("orchestrator", "done", "턴 완료")
    yield {
        "type": "reply",
        "reply": "지금은 데모 모드예요! 🖋️ GEMINI_API_KEY(무료)를 설정하면 실제 LLM이 답변합니다.\n"
                 "시험 문제를 입력하면 라이브러리 적중 시 키 없이도 실제 답안지가 조립돼요 — "
                 "\"SLA에 대하여 설명하시오 (25점)\"처럼 입력해 보세요.",
        "demo": True,
        "llm_calls": 0,
    }


async def _demo_exam() -> AsyncIterator[dict]:
    """라이브러리 미적중 + 키 없음 데모 — 보완 루프 포함 고정 시나리오 (다인부터 이어짐)."""
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
        kind="2교시형(서술)",
        points=25,
        body=_DEMO_BODY,
        mnemonic_html=_DEMO_MNEMONIC,
    )
    vol = _volume(_DEMO_BODY, "2교시형(서술)")  # 데모도 실측 분량 노출
    yield {
        "type": "reply",
        "reply": "라이브러리에 없는 토픽이라 라이브 파이프라인 데모로 안내드려요. 📄\n"
                 "『제로 트러스트 보안 모델』 2교시형(서술) 25점 고정 답안지입니다. "
                 f"세아 채점: 1차 72점 → 보완 1회 → 재채점 91점. "
                 f"분량 {vol['pages']}쪽 {vol['line_in_page']}줄 (환산 {vol['pages_frac']}매).\n"
                 "GEMINI_API_KEY(무료)를 설정하면 입력하신 문제로 진짜 답안을 작성해 드립니다.",
        "demo": True,
        "library": False,
        "exam": {"kind": "2교시형(서술)", "points": 25, "topic": "제로 트러스트 보안 모델"},
        "review": {"score": 91, "rounds": 1, "weak_points": _DEMO_WEAK,
                   "volume": {"lines": vol["lines"], "pages": vol["pages_frac"]}},
        "sheet": {"kind": "2교시형(서술)", "points": 25, "pages": vol["pages"],
                  "lines": vol["line_in_page"], "target_pages": vol["target_pages"]},
        "artifact": {"title": "제로 트러스트 보안 모델", "html": sheet},
        "llm_calls": 0,
    }


# ---------------------------------------------------------------- 파이프라인


def classify_exam(message: str, kind_hint: str | None = None) -> tuple[str, int]:
    """배점·교시형 규칙 판별 (LLM 0콜, 코디 담당). kind_hint는 프런트 수동 선택."""
    m = re.search(r"(\d{1,3})\s*점", message)
    points = int(m.group(1)) if m else None
    if kind_hint and "1교시" in kind_hint:
        kind = "1교시형(용어)"
    elif kind_hint and "2교시" in kind_hint:
        kind = "2교시형(서술)"
    elif points is not None:
        kind = "1교시형(용어)" if points <= 10 else "2교시형(서술)"
    elif re.search(r"약술|정의하|간략히", message):
        kind = "1교시형(용어)"
    else:
        kind = "2교시형(서술)"  # 3·4교시도 2교시형과 동일 취급
    if points is None:
        points = 10 if "1교시" in kind else 25
    return kind, points


def _verify_assembly(body: str, sheet: str, kind: str) -> list[str]:
    """세아 규칙 검증 (0콜): 필수 슬롯/표 최소 2행/암기 박스/외부 리소스 0건 + 형식 린트."""
    warnings = list(_lint_format(body, kind))
    for tbl in re.findall(r"<table.*?</table>", body, re.S | re.I):
        if len(re.findall(r"<tr[\s>]", tbl, re.I)) < 2:
            warnings.append("행 2개 미만 표 존재")
            break
    if re.search(r"https?://|<script", sheet, re.I):
        warnings.append("외부 리소스/스크립트 감지")
    if 'class="mnemonic"' not in sheet:
        warnings.append("암기 박스 누락")
    return warnings


async def run_pipeline(history: list[dict], message: str,
                       kind_hint: str | None = None) -> AsyncIterator[dict]:
    """사용자 메시지 하나를 처리한다.

    1) 비시험 문장 → chat 분기 (라이브 1콜 / 데모)
    2) 시험 문제 → 규칙 판별(0콜) → 라이브러리 매칭(0콜, 모호 시 1콜)
       - 적중 → 부품 조립 (0~2콜, 3초 목표)
       - 미적중 → 라이브 파이프라인 폴백 (최대 5콜) / 키 없으면 데모
    """
    try:
        if not any(w in message for w in EXAM_WORDS):
            async for e in _chat_path(history, message):
                yield e
            return

        kind, points = classify_exam(message, kind_hint)
        yield _ev("orchestrator", "working", "접수 중…")
        yield _talk("orchestrator", f"{points}점 {kind.split('(')[0]} 문제네요. 누리님, 토픽 검색!")
        yield _ev("nlu", "working", "토픽 검색 중…")
        await asyncio.sleep(0.1)

        llm_calls = 0
        res = _library.match(message)
        matched = list(res["matched"])
        if res["status"] == "ambiguous" and provider() is not None:
            # 모호 구간: 후보 목록 내 선택만 허용하는 LLM 1콜
            yield _ev("nlu", "thinking", "후보 확인 중…")
            llm_calls += 1
            try:
                pick = _parse_json(await _chat(
                    "당신은 기술사 답안 팀의 토픽 검색 담당 '누리'입니다. 문제가 가리키는 토픽을 "
                    "후보 목록 안에서만 고르세요. 후보 밖 id 금지, 해당 토픽이 없으면 빈 배열. "
                    "JSON만 출력: {\"ids\": [\"MG-000\", ...]}",
                    [{"role": "user", "content":
                        f"문제: {message}\n후보: {json.dumps(res['candidates'], ensure_ascii=False)}"}],
                    json_mode=True,
                ), {"ids": []})
                matched = _library.pick_from_candidates(pick.get("ids"), res["candidates"])
            except RateLimitError:
                matched = []

        if matched:
            async for e in _assembly_path(message, kind, points, matched, llm_calls):
                yield e
            return

        # ---- 미적중 폴백
        yield _ev("nlu", "done", "미적중")
        if provider() is None:
            yield _talk("nlu", "라이브러리에 없는 토픽이에요. 데모 시나리오로 안내할게요!")
            async for e in _demo_exam():
                yield e
        else:
            yield _talk("nlu", "라이브러리에 없는 토픽이에요. 라이브 파이프라인으로 작성할게요!")
            async for e in _live_exam(message, kind, points, llm_calls):
                yield e
    except RateLimitError:
        yield _ev("orchestrator", "error", "LLM 한도 초과")
        yield {
            "type": "reply",
            "reply": "무료 LLM 한도(분당 토큰)를 초과했어요. 약 1분 후 다시 시도해주세요. "
                     "Groq 대신 Gemini 키를 쓰면 여유가 큽니다.",
            "error": "rate_limited",
        }
    except Exception as exc:
        yield _ev("orchestrator", "error", f"오류: {type(exc).__name__}")
        yield {
            "type": "reply",
            "reply": f"죄송해요, 작업 중 오류가 발생했어요. ({type(exc).__name__}: {exc})",
            "error": str(exc),
        }


async def _chat_path(history: list[dict], message: str) -> AsyncIterator[dict]:
    """일반 질문 — 매칭 시도 없이 로운(수험 멘토)이 바로 답변 (라이브 1콜)."""
    yield _ev("orchestrator", "working", "접수 중…")
    yield _talk("orchestrator", "일반 질문이네요. 로운님이 멘토로 바로 답할게요!")
    if provider() is None:
        async for e in _demo_chat():
            yield e
        return
    yield _ev("writer", "working", "답변 작성 중…")
    reply = await _chat(
        "당신은 '기술사 답안 사무소'의 집필 담당이자 기술사 수험 멘토 '로운'입니다. "
        "기술사 시험 준비(공부법, 답안 작성 요령, 용어 개념, 서브노트 등)에 대해 "
        "친절하고 간결한 한국어로 답하세요. "
        "사용자가 시험 문제를 그대로 입력하면 팀이 답안지를 즉시 만들어 준다는 것도 "
        "필요할 때 자연스럽게 안내하세요.",
        history[-10:] + [{"role": "user", "content": message}],
        max_tokens=2048,
    )
    yield _ev("writer", "done", "답변 완료")
    yield _talk("writer", "답변 보냈어요!")
    yield _ev("orchestrator", "done", "턴 완료")
    yield {"type": "reply", "reply": reply, "llm_calls": 1}


async def _assembly_path(message: str, kind: str, points: int,
                         matched: list[str], llm_calls: int) -> AsyncIterator[dict]:
    """라이브러리 적중 — 부품 조립 경로 (LLM 0~2콜, 3초 목표. sleep은 연출용 ≤0.15s)."""
    is_terms = "1교시" in kind
    topics = [t for t in (_library.load_topic(i) for i in matched) if t]
    names = [short_name(t) for t in topics]

    # 부분 적중 감지: 문제 주제어 중 적중 토픽에 안 잡힌 것
    missing: list[str] = []
    subjects = split_subjects(message)
    if len(subjects) >= 2:
        for s in subjects:
            r = _library.match(s)
            if not (set(r["matched"]) & set(matched)):
                missing.append(s)
    missing = missing[:2]

    yield _ev("nlu", "done", f"적중 {len(topics)}건")
    note = f"라이브러리 {len(topics)}건 적중: {', '.join(names)}"
    if missing:
        note += f" (미등록 {len(missing)}건: {', '.join(missing)})"
    yield _talk("nlu", note)
    await asyncio.sleep(0.12)

    # 다인 — 슬롯 배치 (0콜)
    yield _ev("designer", "working", "편집 중…")
    await asyncio.sleep(0.12)
    result = assemble(message, kind, points, topics, missing, {})
    yield _ev("designer", "done", f"슬롯 {result['slots']}개")
    yield _talk("designer", f"{'1교시형' if is_terms else '2교시형'} 슬롯 {result['slots']}개에 부품 배치했어요")
    await asyncio.sleep(0.12)

    # 로운 — 접합부/미등록 소단락/뼈대 보강 (기본 0콜, 필요 시 콜당 예산 llm_calls<=2)
    yield _ev("writer", "working", "집필 중…")
    # 뼈대 적중(핵심 부품 없음) + 키 있으면 Ⅱ단락(구성도·구성요소) LLM 1콜 보강 (스펙 2-1)
    # 단일 토픽 경로 한정 — 복합 조립은 core_sections를 사용하지 않으므로 콜을 아예 안 쓴다
    # (복합에서 콜을 쓰면 결과가 사장되고 로운 talk이 허위가 됨 — 세아 반려 2026-07)
    core: dict[str, str] = {}
    skeleton = ([t for t in topics if not (t.get("components") or t.get("diagram_html"))]
                if len(topics) == 1 else [])
    if skeleton and provider() is not None and llm_calls < 2:
        llm_calls += 1
        try:
            sk = skeleton[0]
            frag = _extract_body(await _chat(
                "당신은 기술사 답안 팀의 집필 담당 '로운'입니다. 라이브러리에 뼈대만 있는 토픽의 "
                "'구성도 및 구성요소' 단락을 아래 계약(docs/answer-template-spec.md §7)대로 "
                "HTML 프래그먼트로만 출력하세요.\n"
                "구조(순서 고정): <h2>토픽명의 구성도 및 구성요소</h2> → <h3>토픽명의 구성도</h3> → "
                "<div class=\"diagram\">개념도(내부: div.d-row/d-col/d-box(+.soft 점선/.d-hub 중심)/"
                "span.d-arrow(→ ← ↓), 박스 3~6개)</div> → <p class=\"gloss\">– 간글 1줄(40자 이내)</p> → "
                "<h3>토픽명의 구성요소</h3> → <table class=\"t3\"><thead><tr><th>구분</th><th>구성요소</th>"
                "<th>설명</th></tr></thead><tbody><tr class=\"r2\">…</tr> 4행</tbody></table>\n"
                "문체는 개조식(~임/~함), 표 셀 34자 이내. 다른 태그·설명·코드 펜스 금지.",
                [{"role": "user", "content":
                    f"문제: {message}\n토픽명: {short_name(sk)}\n"
                    f"정의: {sk.get('definition') or ''}\n"
                    f"키워드(소재): {json.dumps(sk.get('keywords') or [], ensure_ascii=False)}"}],
                max_tokens=1200,
            ))
            low = frag.lower()
            if ('class="diagram' in frag and 'class="t3' in frag
                    and "<script" not in low and "http" not in low):
                core[sk["id"]] = frag
        except RateLimitError:
            pass  # 보강 실패 시 현행(슬롯 생략 + 경고) 유지
    extra: dict[str, str] = {}
    if missing and provider() is not None:
        llm_calls += 1
        try:
            frag = _extract_body(await _chat(
                "당신은 기술사 답안 팀의 집필 담당 '로운'입니다. 아래 미등록 토픽의 소단락만 "
                "HTML 프래그먼트로 출력하세요. 허용: <h3>토픽명의 개요</h3> + "
                "<p class=\"def\">키워드 나열형 정의(70자 이내, ~임 종결)</p> + "
                "<table class=\"t2\">(구분|설명 헤더 + 2~3행). 다른 태그·설명 금지.",
                [{"role": "user", "content": f"문제: {message}\n미등록 토픽: {missing[0]}"}],
                max_tokens=800,
            ))
            if frag:
                extra[missing[0]] = frag
        except RateLimitError:
            pass  # 플레이스홀더로 대체
    if extra or core:
        result = assemble(message, kind, points, topics, missing, extra, core)
    # 접합부 다듬기(선택): LIBRARY_POLISH=1 + 키 있을 때만 1콜
    if os.environ.get("LIBRARY_POLISH") == "1" and provider() is not None:
        llm_calls += 1
        try:
            polish = _parse_json(await _chat(
                "기술사 답안의 서론 정의 문장을 문제 어구에 맞게 1문장으로 다듬어 JSON만 출력: "
                "{\"definition\": \"...(70자 이내, ~임 종결, 키워드 나열형)\"}",
                [{"role": "user", "content":
                    f"문제: {message}\n현재 정의: {topics[0].get('definition_long') or topics[0].get('definition')}"}],
                json_mode=True, max_tokens=300,
            ), {})
            new_def = str(polish.get("definition") or "").strip()
            if new_def:
                result["body"] = re.sub(
                    r'(<p class="def">\(정의\) ).*?(</p>)',
                    lambda m: m.group(1) + html_mod.escape(new_def) + m.group(2),
                    result["body"], count=1)
        except RateLimitError:
            pass
    yield _ev("writer", "done", "집필 완료")
    notes = []
    if missing:
        notes.append(f"미등록 토픽 {len(missing)}건 " + ("집필했어요" if extra else "플레이스홀더 처리했어요"))
    if core:
        notes.append("뼈대 토픽이라 핵심 섹션(구성도·구성요소)을 집필했어요")
    elif skeleton:
        notes.append("뼈대 토픽이에요 — 키 설정 시 핵심 섹션을 자동 보강해요")
    yield _talk("writer", " · ".join(notes) if notes else "부품이 완전해서 연결부만 다듬었어요")
    await asyncio.sleep(0.12)

    # 세아 — 규칙 검증 (0콜)
    yield _ev("reviewer", "working", "검증 중…")
    await asyncio.sleep(0.12)
    sheet = render_answer(question=message, title=result["title"], kind=kind, points=points,
                          body=result["body"], mnemonic_html=result["mnemonic_html"])
    warnings = result["warnings"] + _verify_assembly(result["body"], sheet, kind)
    yield _ev("reviewer", "done", "통과" if not warnings else f"경고 {len(warnings)}건")
    yield _talk("reviewer", "필수 섹션·암기박스 확인, 통과 ✅" if not warnings
                else f"조립은 통과, 경고 {len(warnings)}건: {warnings[0]}")

    yield _ev("orchestrator", "done", "납품 완료")
    yield _talk("orchestrator", "답안지 납품 완료! 라이브러리 덕에 즉답이었어요 ⚡")

    vol = _volume(result["body"], kind)
    reply = (f"『{result['title']}』 {kind} {points}점 답안지 조립 완료 — "
             f"라이브러리 {len(topics)}건 적중({', '.join(names)}), LLM {llm_calls}콜. "
             f"분량 {vol['pages']}쪽 {vol['line_in_page']}줄 (환산 {vol['pages_frac']}매).")
    if warnings:
        reply += "\n검증 경고: " + " / ".join(warnings[:4])
    yield {
        "type": "reply",
        "reply": reply,
        "exam": {"kind": kind, "points": points, "topic": result["title"]},
        "library": True,
        "matched": matched,
        "llm_calls": llm_calls,
        "review": {"passed": not warnings, "warnings": warnings},
        "sheet": {"kind": kind, "points": points, "pages": vol["pages"],
                  "lines": vol["line_in_page"], "target_pages": vol["target_pages"]},
        "artifact": {"title": result["title"], "html": sheet},
    }


async def _live_exam(message: str, kind: str, points: int,
                     llm_calls: int) -> AsyncIterator[dict]:
    """미적중 폴백 — 라이브 파이프라인 (설계1 + 초안1 + 채점1 + 보완1 + 재채점1 = 최대 5콜)."""
    subjects = split_subjects(message)
    topic = (subjects[0] if subjects else message)[:30]
    is_terms = "1교시" in kind

    # ---- 다인: 답안 구조 설계
    yield _ev("designer", "thinking", "답안 구조 설계 중…")
    llm_calls += 1
    design = _parse_json(
        await _chat(
            "당신은 기술사 답안 팀의 답안 편집 담당 '다인'입니다. 문제를 보고 답안 목차를 "
            "JSON만으로 설계하세요. ITPE 기술사 답안 문법을 따릅니다.\n"
            "- 2교시형(서술, 4단락 고정): Ⅰ.○○의 개요(로드맵 그림+정의+필요성) → Ⅱ.○○의 구성도 및 구성요소"
            "(개념도+간글+9줄 상세표) → Ⅲ.문제가 직접 요구한 사항(요구별 소제목) → Ⅳ.결론 및 전망 4개 섹션.\n"
            "- 1교시형(용어, 3단락): Ⅰ.개요(정의·특징) → Ⅱ.개념도 및 구성요소 → Ⅲ.활용방안/비교 3개 섹션.\n"
            "- 각 섹션의 points는 가나다(가. 나. 다.) 소제목 단위로 작성.\n"
            "형식: {\"outline\": [{\"section\": \"섹션명\", \"points\": [\"다룰 내용\", ...]}, ...], "
            "\"mnemonic\": {\"word\": \"핵심 키워드 두문자\", \"expansion\": [\"두문자 풀이\", ...]}, "
            "\"diagram_idea\": \"개념도 구성 아이디어\", "
            "\"say\": \"집필 담당 로운에게 설계를 전달하는 짧은 한 마디\"}",
            [{"role": "user", "content": f"문제: {message}\n유형: {kind} {points}점 / 주제: {topic}"}],
            json_mode=True,
            max_tokens=1024,  # 목차 JSON은 1024면 충분 (무료 TPM 절약)
        ),
        {"outline": [], "mnemonic": {}, "diagram_idea": ""},
    )
    outline = design.get("outline") or []
    yield _ev("designer", "done", f"목차 {len(outline)}개 섹션" if outline else "설계 완료")
    yield _talk("designer", design.get("say") or "목차 설계 넘겼어요. 로운님, 부탁해요!")

    # ---- 로운: 답안 초안
    writer_system = (
        "당신은 정보관리기술사 답안 작성 전문가 '로운'입니다. "
        "설계된 목차에 따라 기술사 시험 답안 본문을 작성하세요.\n"
        f"{_BODY_RULES}\n"
        "- 코드 펜스나 설명 없이 답안 본문 HTML만 출력하세요."
    )
    draft_brief = (
        f"문제: {message}\n유형: {kind} {points}점 / 주제: {topic}\n"
        f"목차 설계: {json.dumps(outline, ensure_ascii=False)}\n"
        f"개념도 아이디어: {design.get('diagram_idea') or '-'}"
    )
    yield _ev("writer", "working", "답안 작성 중…")
    llm_calls += 1
    body = _extract_body(await _chat(
        writer_system,
        [{"role": "user", "content": draft_brief}],
        max_tokens=4096,  # 무료 TPM 절약 (429 방지)
    ))
    yield _ev("writer", "done", "초안 완료")
    yield _talk("writer", "초안 완성했어요. 세아님, 채점 부탁드립니다!")

    # ---- 세아: 채점 (LLM) + 서버 측 정량 검증(분량·형식 린트)
    # 분량 기준: 1교시형 최소 1.0매/목표 1.4매, 2교시형 최소 2.5매/목표 3.5매.
    # 최소 미달·린트 위반은 점수와 무관하게 보완 강제 (최대 1회).
    min_pages, rec_pages = (1.0, 1.4) if is_terms else (2.5, 3.5)
    vol = _volume(body, kind)
    lint = _lint_format(body, kind)
    reviewer_system = (
        "당신은 기술사 시험 채점위원 '세아'입니다. 답안 본문 HTML을 검토해 JSON만 출력하세요.\n"
        "채점 기준: ① 출제 의도 부합 ② ITPE 목차 완결성(2교시형 Ⅰ~Ⅳ 4단락, 가나다 소제목) "
        "③ 3단표·개념도·간글 활용 ④ 개조식 문체·키워드 가독성 ⑤ 차별화 요소(결론 단락의 알파) "
        "⑥ 계약 준수 — 허용 밖 태그/문단이 있으면 보완(revise) 사유.\n"
        f"{PASS_SCORE}점 이상이면 verdict를 pass, 미만이면 revise로 판정합니다.\n"
        "형식: {\"score\": 0~100 정수, \"verdict\": \"pass|revise\", "
        "\"weak_points\": [\"미흡 항목\", ...], "
        "\"say\": \"팀에게 채점 결과를 알리는 짧은 한 마디(점수 포함)\"}"
    )
    review_brief = f"문제: {message}\n유형: {kind} {points}점\n"

    def _metrics() -> str:
        s = (f"측정 분량: {vol['pages']}쪽 {vol['line_in_page']}줄, 환산 {vol['pages_frac']}매 — "
             f"기준: 최소 {min_pages}매, 목표 {rec_pages}매 이내\n")
        s += ("서버 형식 린트 위반: " + " / ".join(lint) + "\n") if lint else "서버 형식 린트: 통과\n"
        return s

    yield _ev("reviewer", "thinking", "채점 중…")
    llm_calls += 1
    review = _parse_json(
        await _chat(
            reviewer_system,
            [{"role": "user", "content": review_brief + _metrics() + f"\n답안 본문:\n{body[:4000]}"}],
            json_mode=True,
        ),
        {"score": 80, "verdict": "revise", "weak_points": []},
    )
    score = _score_of(review.get("score"))
    weak = [str(w) for w in (review.get("weak_points") or []) if str(w).strip()]
    rounds = 0
    under = vol["pages_frac"] < min_pages

    # ---- 보완 1회 + 재채점: 점수 미달 / 분량 최소 미달 / 린트 위반 시
    if score < PASS_SCORE or under or lint:
        say1 = str(review.get("say") or "").strip()
        if str(score) not in say1:
            say1 = f"1차 채점 {score}점. " + (say1 or f"보완이 필요해요: {' / '.join(weak[:2]) or '완성도 미흡'}")
        picks = []
        if lint:
            picks.append(lint[0] if len(lint) == 1 else f"형식 위반 {len(lint)}건({lint[0]} 등)")
        if under:
            picks.append(f"분량 환산 {vol['pages_frac']}매로 최소 {min_pages}매 미달")
        if picks:
            say1 += " " + " · ".join(picks) + " — 로운님, 보완해주세요!"
        yield _ev("reviewer", "working", f"{score}점 · 보완 요청")
        yield _talk("reviewer", say1)

        yield _ev("writer", "working", "답안 보완 중…")
        fix_notes = f"채점위원 1차 채점 {score}점. 미흡 항목: {json.dumps(weak, ensure_ascii=False)}\n"
        if lint:
            fix_notes += "서버 형식 린트 위반(반드시 전부 해소할 것): " + " / ".join(lint) + "\n"
        if under:
            fix_notes += (f"현재 환산 {vol['pages_frac']}매, 최소 {min_pages}매 — "
                          "Ⅲ단락 표를 확장해 분량을 확보하세요.\n")
        fix_notes += ("위 사항을 반영해 답안 본문 전체를 다시 출력하세요. "
                      "잘 쓴 부분은 유지하고 지적된 부분을 보강합니다.")
        llm_calls += 1
        revised = _extract_body(await _chat(
            writer_system,
            [
                {"role": "user", "content": draft_brief},
                {"role": "assistant", "content": body[:4000]},
                {"role": "user", "content": fix_notes},
            ],
            max_tokens=4096,
        ))
        if revised:
            body = revised
        rounds = 1
        vol = _volume(body, kind)
        lint = _lint_format(body, kind)
        under = vol["pages_frac"] < min_pages
        yield _ev("writer", "done", "보완 완료")
        yield _talk("writer", "지적사항 반영해서 보완했어요. 재채점 부탁해요!")

        yield _ev("reviewer", "thinking", "재채점 중…")
        llm_calls += 1
        review = _parse_json(
            await _chat(
                reviewer_system,
                [{"role": "user", "content":
                    review_brief + _metrics() +
                    f"(보완 후 재채점, 1차 {score}점, 1차 미흡 항목: "
                    f"{json.dumps(weak, ensure_ascii=False)})\n\n답안 본문:\n{body[:4000]}"}],
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

    # 보완 후에도 남은 정량 위반은 weak_points에 명시
    if lint:
        weak += [w for w in lint if w not in weak]
    if under:
        note = f"분량 미달 — 환산 {vol['pages_frac']}매 (최소 {min_pages}매)"
        if note not in weak:
            weak.append(note)

    yield _ev("orchestrator", "done", "납품 완료")
    yield _talk("orchestrator", "답안지 납품 완료! 다들 수고했어요 ☕")

    sheet = render_answer(
        question=message, title=topic, kind=kind, points=points,
        body=body, mnemonic_html=_mnemonic_html(design.get("mnemonic")),
    )
    summary = f"『{topic}』 {kind} {points}점 답안지 완성! 세아 채점 {score}점"
    if rounds:
        summary += f" (보완 {rounds}회 후 재채점)"
    summary += f". 분량 {vol['pages']}쪽 {vol['line_in_page']}줄 (환산 {vol['pages_frac']}매)."
    if score < PASS_SCORE:
        summary += f"\n기준({PASS_SCORE}점) 미달이라 참고용으로 확인해 주세요."
    if lint or under:
        summary += "\n형식 기준 일부 미달 — 아래 보완 포인트를 확인해 주세요."
    if weak:
        summary += f"\n남은 보완 포인트: {' / '.join(weak[:4])}"
    yield {
        "type": "reply",
        "reply": summary,
        "exam": {"kind": kind, "points": points, "topic": topic},
        "library": False,
        "llm_calls": llm_calls,
        "review": {"score": score, "rounds": rounds, "weak_points": weak,
                   "volume": {"lines": vol["lines"], "pages": vol["pages_frac"]}},
        "sheet": {"kind": kind, "points": points, "pages": vol["pages"],
                  "lines": vol["line_in_page"], "target_pages": vol["target_pages"]},
        "artifact": {"title": topic, "html": sheet},
    }
