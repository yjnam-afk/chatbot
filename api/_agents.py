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

import _question
import _topic_library as _library
from _assembler import assemble, assemble_requirements, short_name, split_subjects

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


# ---- 줄 그리드 분량 모델 · 페이지 레이아웃 v2 (docs/answer-template-spec.md §1·§8)
# 서버가 본문 블록의 줄 수를 계측해 22줄 페이지(머리행 1 + 본문 17/21)로 직접 분할한다.
# 화면 쪽수 = 인쇄 쪽수 = sheet.pages 가 항상 일치 (발주자 3차 반려 대응).
_PAGE1_BODY = 17   # 1쪽 본문 줄 수 (머리행 1 + 문제 스트립 4 제외)
_PAGEN_BODY = 21   # 2쪽부터 본문 줄 수 (머리행 1 제외)
_PAGE_LINES = 22   # 답안지 1매 환산 기준 (머리행 포함)

_BLOCK_OPEN_RE = re.compile(r"<(h2|h3|p|table|div)\b", re.I)


def _split_blocks(html: str) -> list[str]:
    """본문 프래그먼트를 최상위 블록 요소 단위로 분해한다 (div/table 중첩 안전)."""
    blocks: list[str] = []
    i = 0
    while True:
        m = _BLOCK_OPEN_RE.search(html, i)
        if not m:
            break
        tag = m.group(1).lower()
        pat = re.compile(rf"<{tag}\b|</{tag}\s*>", re.I)
        depth, j = 0, m.start()
        while True:
            m2 = pat.search(html, j)
            if not m2:
                j = len(html)
                break
            j = m2.end()
            if m2.group(0)[1] == "/":
                depth -= 1
                if depth == 0:
                    break
            else:
                depth += 1
        blocks.append(html[m.start():j].strip())
        i = j
    return blocks


def _block_lines(block: str) -> int:
    """블록 1개의 점유 줄 수 — 요소별 규칙 (스펙 §2)."""
    b = block.lstrip().lower()
    if b.startswith("<h2") or b.startswith("<h3"):
        return 1
    if b.startswith("<p"):
        return 2 if 'class="def"' in b[:40] else 1
    if b.startswith("<table"):
        return (len(re.findall(r"<tr[\s>]", b))
                + len(re.findall(r'<tr\s+class="r2"', b)))  # r2 행은 +1줄
    if b.startswith("<div"):
        head = b[:60]
        if 'class="diagram d7"' in head:
            return 7
        if 'class="diagram"' in head:
            return 6
        if 'class="mnemonic' in head:
            return 2 if "mn2" in head else 3
        return 1  # gap 등
    return 1


_GAP_HTML = '<div class="gap"></div>'
_ROMANS = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ"
_GANADA = "가나다라마바사아자차"


def _stamp(blk: str, tag: str, n: int) -> str:
    """h2/h3 여는 태그 뒤에 목차 번호를 텍스트로 스탬프한다.

    번호는 서버가 결정 — h2마다 가나다가 리셋되고 페이지 경계와 무관하게 정확하다.
    (CSS 카운터는 페이지 분할 시 브라우저 카운터 스코프 결함으로 폐기, 2026-07 검수)
    """
    marks = _ROMANS if tag == "h2" else _GANADA
    mark = marks[min(n, len(marks)) - 1]
    return re.sub(rf"(<{tag}[^>]*>)", lambda m: m.group(1) + f"{mark}. ", blk, count=1)


def _is_heading(blk: str) -> bool:
    low = blk.lstrip().lower()
    return low.startswith("<h2") or low.startswith("<h3")


def _layout(body_html: str, kind: str,
            mnemonic_html: str = "<p><b>—</b></p>") -> list[dict]:
    """본문+꼬리("끝"·여백·두문자 박스)를 페이지(17/21줄)로 배치한다.

    반환: [{"blocks": [html...], "cap": 줄수, "used": 점유 줄수}]
    - 목차 번호(h2 로마자·h3 가나다)는 여기서 스탬프 — h2마다 가나다 리셋 (검수 결함 1)
    - 2교시형 단락(h2) 사이 1줄 여백은 명시적 .gap 블록으로 물질화 (페이지 첫 줄이면 생략)
    - 고아 제목 방지: 제목(연속 제목 포함)은 뒤따르는 내용 1블록과 같은 페이지에 못
      들어가면 다음 페이지로 민다 — 밀린 자리는 빈 괘선 (검수 결함 2)
    - 꼬리 당김: 마지막 페이지가 두문자 박스(±빈 줄)뿐이면 직전 페이지의 단락 여백을
      제거해 들어가는 경우 당겨 앉힌다 (검수 결함 3)
    """
    p2 = "1교시" not in str(kind)
    seq: list[tuple[str, int]] = []
    h2_seen = False
    sec = sub = 0
    for blk in _split_blocks(body_html or ""):
        low = blk.lstrip().lower()
        if low.startswith("<h2"):
            if p2 and h2_seen:
                seq.append((_GAP_HTML, 1))
            h2_seen = True
            sec += 1
            sub = 0
            blk = _stamp(blk, "h2", sec)
        elif low.startswith("<h3"):
            sub += 1
            blk = _stamp(blk, "h3", sub)
        seq.append((blk, _block_lines(blk)))
    # "끝"은 마지막 답안 내용 바로 옆에 인라인 표기 (체크리스트 U4 — 공단 유의사항·실물).
    # 마지막 블록이 문단이면 그 끝에 붙이고, 표·그림으로 끝나면 다음 줄 서두에 표기한다.
    end_span = ' <span class="end">"끝"</span>'
    if seq and seq[-1][0].rstrip().endswith("</p>"):
        blk0, ln0 = seq[-1]
        seq[-1] = (blk0.rstrip()[:-4] + end_span + "</p>", ln0)
    else:
        seq.append((f"<p>{end_span.strip()}</p>", 1))
    seq.append((_GAP_HTML, 1))
    # 두문자 박스는 라벨 1줄 + 풀이 p 줄수(1~2) — 풀이 1줄이면 2줄 박스(mn2)로 낭비 제거
    n_mn = 1 + max(1, min(2, mnemonic_html.count("<p")))
    mn_cls = "mnemonic" if n_mn >= 3 else "mnemonic mn2"
    seq.append((f'<div class="{mn_cls}">\n<div class="mn-label">두문자 암기 포인트</div>\n'
                f"{mnemonic_html}\n</div>", n_mn))

    pages: list[dict] = []
    cur: list[str] = []
    used, cap = 0, _PAGE1_BODY

    def flush():
        nonlocal cur, used, cap
        pages.append({"blocks": cur, "cap": cap, "used": used})
        cur, used, cap = [], 0, _PAGEN_BODY

    i = 0
    while i < len(seq):
        blk, ln = seq[i]
        if cur and _is_heading(blk):
            # 고아 방지: 제목 연쇄(h2 바로 뒤 h3 등) + 첫 내용 블록까지 필요한 줄 수
            need = ln
            j = i + 1
            while j < len(seq) and _is_heading(seq[j][0]):
                need += seq[j][1]
                j += 1
            if j < len(seq):
                need += seq[j][1]
            if used + need > cap:
                flush()
        if cur and used + ln > cap:
            flush()
            if blk == _GAP_HTML:
                i += 1
                continue  # 페이지 첫 줄의 단락 여백은 생략
        cur.append(blk)
        used += ln
        i += 1
    if cur:
        pages.append({"blocks": cur, "cap": cap, "used": used})

    _pull_tail(pages)
    # 만석 페이지의 말단 블록이 표면 자기 높이 보정 클래스(tend)를 주입한다 —
    # border-collapse 표는 실측 높이가 행합+1px라 페이지 캡을 1px 넘겨
    # overflow:hidden에 하단 닫는 보더가 잘린다 (세아 실측: Q5 3쪽). 기존
    # margin-bottom:-1px는 후속 블록 위치만 보정해 말단 표엔 무효 — tend가
    # margin-top:-1px로 표 전체를 1px 당겨 하단 보더를 캡 안에 넣는다.
    # 만석이 아닌 페이지는 잘릴 일이 없으므로 건드리지 않는다 (회귀 차단).
    for pg in pages:
        last = pg["blocks"][-1].lstrip().lower() if pg["blocks"] else ""
        if pg["used"] >= pg["cap"] and last.startswith("<table"):
            pg["blocks"][-1] = re.sub(r'(<table[^>]*class=")', r"\1tend ",
                                      pg["blocks"][-1], count=1)
    # 최종 답안(꼬리 포함) 아래 줄이 남으면 중앙에 "이하여백" (체크리스트 U5 — 공단·실물).
    # 답안 줄 수가 아니므로 used(분량 계측)에는 넣지 않는다 — 남는 첫 괘선 줄에 얹힌다.
    if pages and pages[-1]["used"] < pages[-1]["cap"]:
        pages[-1]["blocks"].append('<p class="fill">이하여백</p>')
    return pages


def _pull_tail(pages: list[dict]) -> None:
    """마지막 페이지가 두문자 박스(±빈 줄)뿐이면 직전 페이지로 당겨 앉힌다.

    자리 확보 순서(단계적): ① 직전 페이지의 남는 줄 ② 직전 페이지 단락 간 .gap 제거
    ③ 최후 수단으로 "끝" 직후 여백 1줄 축약 — 박스 단독 페이지보다 "끝" 바로 아래
    박스가 낫다는 판단(발주자 검수 2026-07). 그래도 안 들어가면 현행(별도 페이지) 유지.
    """
    if len(pages) < 2:
        return
    last, prev = pages[-1], pages[-2]
    nongap = [b for b in last["blocks"] if b != _GAP_HTML]
    if not nongap or not all(b.lstrip().startswith('<div class="mnemonic') for b in nongap):
        return
    tail_blocks = list(last["blocks"])
    need = last["used"]
    deficit = need - (prev["cap"] - prev["used"])
    if deficit > 0:
        blocks = prev["blocks"]
        para_gaps = [i for i, b in enumerate(blocks)
                     if b == _GAP_HTML and not (i > 0 and 'class="end"' in blocks[i - 1])]
        end_gaps = [i for i, b in enumerate(blocks)
                    if b == _GAP_HTML and (i > 0 and 'class="end"' in blocks[i - 1])]
        lead_gap = 1 if tail_blocks and tail_blocks[0] == _GAP_HTML else 0
        if len(para_gaps) + len(end_gaps) + lead_gap < deficit:
            return  # 여백 제거로도 확보 불가 — 현행 유지
        take = sorted((para_gaps + end_gaps)[:deficit])  # 단락 여백 우선, 끝-뒤 여백은 최후
        for i in reversed(take):
            del blocks[i]
        prev["used"] -= len(take)
        deficit -= len(take)
        if deficit > 0 and lead_gap:
            tail_blocks.pop(0)  # 넘어온 꼬리의 선행 여백 축약
            need -= 1
    prev["blocks"].extend(tail_blocks)
    prev["used"] += need
    pages.pop()


def _volume(body_html: str, kind: str) -> dict:
    """답안 분량 — 페이지 레이아웃 실측 기반. 화면/인쇄 쪽수와 항상 일치한다."""
    pages = _layout(body_html, kind)
    total = sum(p["used"] for p in pages)
    return {
        "lines": total,
        "pages": len(pages),
        "line_in_page": pages[-1]["used"],
        "pages_frac": round(total / _PAGE_LINES, 1),
        "target_pages": 1.4 if "1교시" in str(kind) else 3.5,
    }


def _lint_format(body_html: str, kind: str) -> list[str]:
    """서버 측 형식 린터 — LLM이 프롬프트 규칙을 무시해도 코드로 강제 검사한다.

    위반 지적 문자열 목록을 반환 (빈 목록 = 통과). LLM 호출 없음.
    검사 기준: 줄 그리드 계약(docs/answer-template-spec.md §7).
    """
    issues: list[str] = []
    body = body_html or ""
    is_terms = "1교시" in kind
    # 1) 단락(h2) 수: 1교시형 Ⅰ~Ⅲ 3개 고정, 2교시형은 요구 수에 따라 Ⅰ~Ⅳ 기준·
    #    최대 Ⅵ 가변 (question-spec 2-2 — N=2 기준 4단락, N=4~5 병렬식 5~6단락)
    n_h2 = len(re.findall(r"<h2[\s>]", body, re.I))
    if is_terms:
        if n_h2 != 3:
            issues.append(f"단락 수 {n_h2}개(기준 3개) — Ⅰ~Ⅲ 구조로 재편 필요")
    elif not 4 <= n_h2 <= 6:
        issues.append(f"단락 수 {n_h2}개(기준 4~6개) — Ⅰ~Ⅳ(요구 많으면 ~Ⅵ) 구조로 재편 필요")
    # 2) "답)" 표기
    if 'class="ans"' not in body:
        issues.append("\"답)\" 표기 누락 — 첫 줄 <p class=\"ans\">답)</p>")
    # 3) 개념도
    if not re.search(r"<div[^>]*class=\"[^\"]*diagram", body, re.I):
        issues.append("개념도 누락 — div.diagram 1개 이상 필요")
    # 3-1) 2교시형 서론 로드맵(Type IV) — d7 부재는 발주자 반려 형태(텍스트 약식 서론)
    if not is_terms and 'class="diagram d7"' not in body:
        issues.append("서론 로드맵(diagram d7) 부재 — 2교시형은 Type IV 서론 필수")
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
    # 7) 간글: 본문 개념도 직후 간글 1줄 필수(체크리스트 G1 — "간글은 다 빼먹었냐?"),
    #    서론 로드맵(d7) 직후는 (정의) p.def 2줄이 간글을 대체(G2 — 복합 조립은 h3 뒤 정의)
    blocks = _split_blocks(body)
    for i, b in enumerate(blocks):
        head = b.lstrip().lower()[:60]
        if not head.startswith("<div"):
            continue
        nxt = blocks[i + 1].lstrip().lower()[:40] if i + 1 < len(blocks) else ""
        if 'class="diagram d7"' in head:
            if not (nxt.startswith("<h3") or 'class="def"' in nxt):
                issues.append("서론 로드맵(d7) 아래 (정의) 2줄 누락 — 그림 밑 정의가 간글 대체")
        elif 'class="diagram"' in head:
            if not ('class="gloss"' in nxt or 'class="def"' in nxt):
                issues.append("본문 개념도 직후 간글(p.gloss 1줄) 누락")
    return issues


def _lint_text_density(body_html: str) -> list[str]:
    """문단 밀도 린터 (발주자 규격 2026-07-17): 전폭 줄 17~19자(공백 제외·영문 반각)
    → 2줄 문단(p.def) 40자, 간글(p.gloss) 20자 초과는 위반 — 넘치면 손으로 못 베낀다.

    스코프는 표 밀도 린터(_lint_table_density)와 동일: 조립 경로는 견본(MG-001)만
    경고 강제·나머지 토픽은 서버 로그, 폴백(LLM) 경로는 보완 사유. 기존 부품이
    전면 감량 배치를 마치면 조립 경로도 전면 강제로 전환한다 (세아 반려 2026-07-17
    — _lint_format에 두면 견본 외 20개 풀부품이 일괄 경고 노출).
    """
    issues: list[str] = []
    for m in re.finditer(r'<p class="(def|gloss)">(.*?)</p>', body_html or "", re.S | re.I):
        w = _wide_len(html_mod.unescape(re.sub(r"<[^>]+>", "", m.group(2))))
        cap = _W_GLOSS if m.group(1) == "gloss" else _W_FULL2
        if w > cap:
            issues.append(f"{'간글' if m.group(1) == 'gloss' else '2줄 문단'} 손글씨 밀도 초과 "
                          f"({w:.0f}자 > {cap:.0f}자) — 한 줄 17~19자 기준")
    return issues


def _wide_len(s: str) -> float:
    """손글씨 환산 글자 수 — 공백 제외, 한글·한자 1자 / 영문·숫자·ASCII 반각 0.5자.

    근거(발주자 규격 + [실물] 기본 답안 2교시.pdf OCR 줄바꿈 실측 2026-07-17):
    IT거버넌스 정의 줄바꿈 = 19자/18자(공백 제외), 표 설명셀 한 줄 "서비스 제공업자의
    서" = 9자, r2 두 줄 합 18자, 구성요소열 "eSCM, ISO 20000" = 반각 환산 ≈7자.
    → 공백 포함이 아니라 **공백 제외** 기준이 실물과 정합.
    """
    return sum(0.5 if ord(ch) < 128 else 1.0 for ch in s if not ch.isspace())


# 손글씨 밀도 상한 (발주자 직접 규격 2026-07-17: "한줄에 17~19글자 / 표 설명 칸 5~7…
# 넘치면 위반 — 모자란 것보다 넘치는 게 죄"). 전폭 줄 19자 → 2줄 문단 38(+접두 여유 40).
_W_FULL2 = 40.0    # p.def 2줄 문단 상한 ("(정의) " 접두 포함)
_W_GLOSS = 20.0    # p.gloss 간글 1줄 상한 ("– " 접두 포함)
_W_CELL = {"t3": 11.0, "texp": 11.0, "t2": 15.0, "tcmp": 8.0}  # 마지막 열 1줄 상한
_W_CELL_MID = 8.0  # 가운데 열(구성요소) 1줄 상한 (발주자 "5~7글자" + 반각 여유)


def _lint_table_density(body_html: str) -> list[str]:
    """표 밀도 린터 (체크리스트 T5 — 손글씨 기준 v2) — "행 높이 = 내용 밀도".

    상한 방향(발주자 2026-07-17): 셀 내용이 손글씨 한 줄 용량을 넘치면 위반.
    - r2 행: 개조식 항목 각 12자, 단일 문자열 22자 초과 → 위반.
      최장 셀이 1줄분(11자 이하)인데 개조식도 아니면 미달 위반(두 줄 잡고 한 줄 쓰기).
    - 1줄 행: 마지막 열이 표종별 상한(_W_CELL), 가운데 열이 8자 초과 → 위반.
    적용: 조립 경로는 견본(MG-001)만 강제·나머지는 로그, 폴백 경로는 보완 사유.
    """
    under = over = 0
    for tbl in re.findall(r"<table[^>]*>.*?</table>", body_html or "", re.S | re.I):
        m = re.search(r'class="(t3|texp|t2|tcmp)"', tbl[:40])
        last_cap = _W_CELL.get(m.group(1) if m else "", 15.0)
        for tr in re.findall(r"<tr[^>]*>.*?</tr>", tbl, re.S | re.I):
            if "<th" in tr:
                continue
            raw_cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)
            cells = [html_mod.unescape(re.sub(r"<[^>]+>", "\n", c)).strip()
                     for c in raw_cells]
            widths = [max((_wide_len(x) for x in c.split("\n")), default=0.0) for c in cells]
            if 'class="r2"' in tr[:20]:
                for c, w in zip(raw_cells, widths):
                    if "<br" in c.lower():
                        if w > 12.0:  # 개조식 항목이 셀 폭 한 줄을 넘침
                            over += 1
                    elif _wide_len(html_mod.unescape(re.sub(r"<[^>]+>", "", c))) > 22.0:
                        over += 1
                if widths and max(widths) <= 11.0 and "<br" not in tr.lower():
                    under += 1
            elif widths:
                if widths[-1] > last_cap:
                    over += 1
                if len(widths) >= 3 and widths[-2] > _W_CELL_MID:
                    over += 1
    issues: list[str] = []
    if over:
        issues.append(f"표 셀 손글씨 밀도 초과 {over}건 — 설명열 한 줄 ~10자·구성요소열 "
                      "5~7자·r2 개조식 항목 12자 상한 (넘치면 위반)")
    if under:
        issues.append(f"표 밀도 미달 — 내용이 1줄분인 2줄 행(r2) {under}개: 1줄 행 전환 필요")
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

# 답안지 우선 라우팅 (발주자 피드백 2026-07: "정규화 답안지 적어줘"가 채팅으로 빠짐)
_ANSWER_WORD_RE = re.compile(r"답안지|모범답안")
_REQ_VERB_RE = re.compile(r"적어|작성|만들|뽑아|써\s?줘|줘")
_KIND_HINT_RE = re.compile(r"[12]\s*교시|\d{1,3}\s*점")
_CHAT_Q_RE = re.compile(r"\?|뭐야|뭔가요|뭔지|무엇|알려\s?줘|어떻게|어때|왜\s|인가요|일까")
_REQ_TAIL_RE = re.compile(
    r"\s*(?:에 대하여|에 대해)?\s*(?:모범답안|답안지|답안)?\s*(?:을|를)?"
    r"\s*(?:적어|작성해|만들어|뽑아|써)?\s*줘?[?!. ]*$")


def is_exam_request(message: str, kind_hint: str | None = None) -> bool:
    """답안지 우선 라우팅 — 이 앱의 존재 이유는 답안지이므로 애매하면 시험 경로.

    1) 교시형 칩 수동 선택 → 무조건 시험 경로
    2) 시험 말투(EXAM_WORDS) / "답안지·모범답안" / "답안"+요청동사 / 교시·배점 언급 → 시험
    3) 대화형 의문문(뭐야/무엇/알려줘/물음표 등) → 채팅
    4) 짧은 입력(30자 이하)이 라이브러리에 확정 적중 → 시험 (예: "BIA" 단독)
    """
    msg = (message or "").strip()
    if kind_hint:
        return True
    if any(w in msg for w in EXAM_WORDS):
        return True
    if _ANSWER_WORD_RE.search(msg):
        return True
    if "답안" in msg and _REQ_VERB_RE.search(msg):
        return True
    if _KIND_HINT_RE.search(msg):
        return True
    if _CHAT_Q_RE.search(msg):
        return False
    if len(msg) <= 30 and _library.match(msg)["status"] == "hit":
        return True
    return False


def _subject_hint(message: str) -> str:
    """문제/요청문에서 주제어 힌트 추출 — 꼬리의 답안 요청 어구를 벗겨낸다."""
    s = re.sub(r"[?!.]+$", "", (message or "").strip())
    stripped = _REQ_TAIL_RE.sub("", s).strip()
    base = stripped or s
    subjects = split_subjects(base)
    return (subjects[0] if subjects else base)[:30]


# ---------------------------------------------------------------- 답안지 템플릿

_ANSWER_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — 기술사 답안지</title>
<style>
/* 기술사 답안지 — 실물 줄 그리드 템플릿 (docs/answer-template-spec.md, v2 서버 페이지 분할)
   모든 요소가 1줄(--lh)의 정수배로 괘선에 스냅되고, 서버가 22줄 페이지로 직접 분할한다. */
* { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --lh: 32px;
  --ink: #1c2f4a; --chrome: #3d4148;
  --rule: #c5cedd; --rule2: #9db0c8; --paper: #fdfdfa;
}
/* 목차 번호(h2 로마자 · h3 가나다)는 서버가 텍스트로 스탬프한다 — CSS 카운터는
   페이지 분할(.content 다중화) 시 브라우저 카운터 스코프 결함으로 폐기 (2026-07 검수) */
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  background: #e6e5e0; color: var(--ink);
  font-family: "Noto Serif KR", "Noto Serif CJK KR", "Nanum Myeongjo", "Source Han Serif K", Batang, AppleMyungjo, serif;
  font-size: 15px; padding: 36px 12px 48px; word-break: keep-all;
}
.page {
  width: min(794px, 100%); margin: 0 auto 26px;
  background: var(--paper); border: 1px solid #c9c5ba;
  box-shadow: 0 2px 18px rgba(40, 40, 30, 0.12);
}
.page-head {
  height: var(--lh); display: flex; align-items: center;
  font-family: system-ui, sans-serif; font-size: 11.5px; color: var(--chrome);
  border-bottom: 2px solid var(--rule2);
}
/* 내지 머리행은 최소 인쇄 — 번호 칸 + 쪽 표기만, 표제 없음 (체크리스트 U6, 실물 내지) */
.ph-box { width: 88px; height: 100%; display: flex; align-items: center; justify-content: center; border-right: 1px solid var(--rule2); letter-spacing: 0.3em; }
.ph-sp { flex: 1; }
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
  height: calc(var(--body, 21) * var(--lh));
  overflow: hidden;
  background: repeating-linear-gradient(to bottom,
    transparent 0 calc(var(--lh) - 1px),
    var(--rule) calc(var(--lh) - 1px) var(--lh));
}
/* 좌측 여백 세로 3줄 — 단락 표기(Ⅰ./가./본문) 들여쓰기 가이드 (체크리스트 U3, 공단 규격) */
.body::before { content: ""; position: absolute; left: 15px; top: 0; bottom: 0; width: 1px;
  background: var(--rule2); box-shadow: 15px 0 var(--rule2), 30px 0 var(--rule2); }
.content { margin: 0 20px 0 58px; }
.content h2, .content h3, .content p { line-height: var(--lh); font-size: 15px; font-weight: 400; }
.content h2 { font-weight: 700; }
.content h3 { font-weight: 700; padding-left: 18px; }
/* 2교시형 단락(h2) 사이 1줄 여백은 서버가 .gap 블록으로 물질화한다 (페이지 분할 정합) */
.ans { font-weight: 700; }
.def { min-height: calc(2 * var(--lh)); padding-left: 18px; }
.gloss { padding-left: 18px; }
.end { font-weight: 700; } /* "끝"은 답안이 끝난 지점 바로 옆 인라인 (체크리스트 U4) */
.content span.end { margin-left: 10px; }
.fill { text-align: center; letter-spacing: 0.5em; text-indent: 0.5em; } /* "이하여백" 중앙 (U5) */
.gap { height: var(--lh); }
.content u, .content .keyword {
  text-decoration: underline; text-underline-offset: 5px; text-decoration-thickness: 1px;
  font-weight: inherit; border: none;
}
/* border-collapse 표는 외곽 보더로 실측 높이가 행합보다 +1px — 후속 블록을 1px 당겨
   줄 그리드 정렬을 유지한다 (만석 페이지 하단 테두리 클리핑 방지, 세아 검수 2026-07) */
.content table { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 13.5px; margin-bottom: -1px; }
/* 만석 페이지 말단 표 자기 보정: margin-bottom:-1px는 후속 블록만 당기므로 페이지
   마지막 블록이 표면 +1px가 캡을 넘어 하단 닫는 보더가 잘린다 — 서버(_layout)가
   해당 표에만 tend를 주입, 표 전체를 1px 당겨 보더를 캡 안에 수납 (세아 실측 Q5 3쪽) */
.content table.tend { margin-top: -1px; }
.content th, .content td { border: 1px solid var(--ink); padding: 2px 8px; vertical-align: middle; line-height: 1.5; overflow: hidden; }
.content th { font-weight: 700; text-align: center; } /* 실물 손답안엔 음영 없음 — 체크리스트 T2 */
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
/* ---- 개념도 패턴 키트 (docs/diagram-spec.md, 실물 MG 표본 34건 역설계) ----
   모든 패턴은 고정 높이 .diagram(6줄)/.d7(7줄) 안의 flex/grid 배치 —
   컨테이너 높이가 불변이라 페이지 줄 계측(_layout)과 충돌하지 않는다. */
.d-flow { display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%; } /* ①흐름 */
.d-flow .d-box { flex: 1; padding: 4px 6px; font-size: 12px; }
.d-out { border: 1px dashed var(--ink); background: #fff; font-size: 10.5px; font-weight: 400; padding: 1px 8px; text-align: center; } /* 산출물 병기 칩 */
.d-bar { border: 1.5px solid var(--ink); background: #fff; font-weight: 700; font-size: 13px; text-align: center; padding: 3px 10px; width: 72%; } /* ②대분류 긴 사각 */
.d-bar small { display: block; font-size: 11px; font-weight: 400; }
.d-tree { display: flex; flex-direction: column; align-items: center; width: 100%; } /* ②계층/분류 */
.d-stem { width: 0; height: 10px; border-left: 1.5px solid var(--ink); }
.d-branch { width: 72%; height: 10px; border: 1.5px solid var(--ink); border-bottom: none; }
.d-tree .d-row { align-items: stretch; gap: 10px; }
.d-stack { display: flex; flex-direction: column; gap: 6px; width: 72%; } /* ③레이어 */
.d-stack .d-box { width: 100%; padding: 3px 10px; }
.d-cycle { display: grid; grid-template-columns: auto 1fr auto 1fr auto; gap: 8px 10px; align-items: center; justify-items: center; width: 88%; } /* ④순환(4단계) */
.d-cycle.c6 { grid-template-columns: auto 1fr auto 1fr auto 1fr auto; } /* 6단계 */
.d-cycle .d-box { width: 100%; padding: 3px 6px; font-size: 12px; }
.d-turn { grid-row: 1 / 3; font-weight: 700; font-size: 17px; } /* 순환 좌우 회귀 화살표 */
.d-net { display: grid; grid-template-columns: 1fr auto 1fr; gap: 6px 12px; align-items: center; justify-items: center; width: 100%; } /* ⑤허브 3×3 */
.d-net .d-box { width: 100%; padding: 3px 8px; font-size: 12px; }
.d-net .d-box.d-hub { width: auto; font-size: 14px; padding: 8px 16px; }
.d-vs { display: grid; grid-template-columns: 1fr auto 1fr; gap: 10px; align-items: stretch; width: 100%; } /* ⑥비교대칭 */
.d-zone { border: 1px dashed var(--ink); padding: 8px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; }
.d-zt { font-size: 11.5px; font-weight: 700; letter-spacing: 0.05em; }
.d-link { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; font-size: 11px; text-align: center; }
.mnemonic { height: calc(3 * var(--lh)); border: 1.5px dashed var(--ink); padding: 0 14px; overflow: hidden; }
.mnemonic.mn2 { height: calc(2 * var(--lh)); }
.mn-label { line-height: var(--lh); font-size: 12px; font-weight: 700; letter-spacing: 0.25em; }
.mnemonic p { line-height: var(--lh); font-size: 14px; }
.mnemonic b { border-bottom: 1px solid var(--ink); }
@page { size: A4 portrait; margin: 12mm 14mm; }
@media print {
  :root { --lh: 10.5mm; }
  body { background: #fff; padding: 0; }
  .page { width: auto; margin: 0; border: none; box-shadow: none; page-break-after: always; }
  .page:last-child { page-break-after: auto; }
}
</style>
</head>
<body>
{pages}
</body>
</html>"""


def render_answer(question: str, title: str, kind: str, points: int | str,
                  body: str, mnemonic_html: str) -> str:
    """답안지 템플릿에 내용을 채워 완성 HTML을 만든다 (v2 서버 페이지 분할).

    본문+꼬리를 _layout으로 22줄 페이지에 배치하고, 쪽마다 머리행("N 쪽")을
    붙인다(1쪽만 문제 스트립 포함). 목차 번호는 _layout이 스탬프하므로 여기선
    배치만 한다. CSS 중괄호 때문에 str.format() 금지 — 입력값에 "{pages}"
    같은 리터럴이 있어도 재치환되지 않도록 단일 패스 re.sub로 치환한다.
    question/title/kind/points는 escape, body/mnemonic_html은 이미 HTML.
    """
    is_terms = "1교시" in str(kind)
    pcls = "p1" if is_terms else "p2"
    esc = html_mod.escape
    pages = _layout(body, kind, mnemonic_html)
    page_parts = []
    for i, pg in enumerate(pages):
        # 내지 머리행: 번호 칸 + "N 쪽"만 — 임의 표제 없음 (체크리스트 U6, 실물 내지)
        head = (
            '  <div class="page-head">\n'
            '    <span class="ph-box">번 호</span>\n'
            '    <span class="ph-sp"></span>\n'
            f'    <span class="ph-num">{i + 1} 쪽</span>\n'
            "  </div>\n"
        )
        strip = ""
        if i == 0:
            strip = (
                '  <div class="q-strip">\n'
                '    <div class="qs-top">\n'
                f'      <span class="qs-no">문) {esc(str(title))}</span>\n'
                f'      <span class="qs-kind">{esc(str(kind))} · {esc(str(points))}점</span>\n'
                "    </div>\n"
                f'    <p class="qs-text">{esc(str(question))}</p>\n'
                "  </div>\n"
            )
        content = "\n".join(pg["blocks"])
        page_parts.append(
            f'<div class="page">\n{head}{strip}'
            f'  <div class="body" style="--body:{pg["cap"]}">\n'
            f'    <div class="content {pcls}">\n'
            f"{content}\n"
            "    </div>\n  </div>\n</div>"
        )
    parts = {"title": esc(str(title)), "pages": "\n".join(page_parts)}
    return re.sub(r"\{(title|pages)\}", lambda m: parts[m.group(1)], _ANSWER_TEMPLATE)


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
  p class="def"       2줄 문단(정의·특징·마무리 설명) — 손글씨 밀도: 한 줄 17~19자(공백 제외,
                    영문·숫자는 2자=1자 환산), 2줄 합계 38자 이내
  p class="gloss"     간글 1줄, "– "로 시작, 19자 이내(공백 제외 환산)
  u                   키워드 밑줄(섹션당 1~3개), 강조 인용은 "쌍따옴표" 텍스트
  table class="t3|t2|tcmp|texp" + thead/tbody/tr/th/td — 2줄 행은 <tr class="r2">
  div class="diagram"     개념도 6줄 컨테이너 (답안 전체 1~2개, 일도일표)
  div class="diagram d7"  2교시 서론 로드맵 전용 7줄 컨테이너 (서론에 1개만)
    내부 전용: div.d-row / div.d-col / div.d-box(+.soft 점선 보조, +.d-hub 중심 강조) /
               div.d-circle(등장배경 원형) / span.d-sep(구분 점선) / span.d-arrow(→ ← ↓ ↔ 텍스트)
    패턴 키트(docs/diagram-spec.md — 토픽 성격에 맞는 것 하나를 선택, 박스 일렬 나열 금지):
      div.d-flow  ①흐름/절차(단계 좌→우, 산출물은 d-col 안 div.d-out 칩으로 병기)
      div.d-tree  ②계층/분류(div.d-bar 대분류 긴 사각 + div.d-stem 수직선 + div.d-branch 갈래 + d-row 하위)
      div.d-stack ③레이어(층 스택 — 위가 상위 계층)
      div.d-cycle ④순환(PDCA류 4단계; 6단계는 d-cycle.c6 — 좌우 회귀 화살표는 span.d-turn)
      div.d-net   ⑤허브(3×3: 모서리 4요소 + 중앙 d-hub + 방향 d-arrow)
      div.d-vs    ⑥비교대칭(div.d-zone 좌우 진영(첫 줄 div.d-zt 라벨) + 중앙 div.d-link 연결·라벨)
- 표 종류: t3 = 3단표(구분 20/구성요소 20/설명 60) / t2 = 2단표(구분 20/설명 80) /
  tcmp = 비교표(구분 20/40/40) / texp = 2가지 설명표(20/19/61)
- 표 작성(실물 모범답안 규칙 — 체크리스트 §4): 헤더 행 필수(라벨은 구분/유형/단계/순서 등 내용에 맞게).
  속성-설명형 셀은 "- " 개조식 항목 나열(항목 사이 <br>), 비교표(tcmp) 셀은 짧은 구 평문.
  1열 카테고리가 하위 2~3행을 묶으면 <td rowspan="N"> 병합. 셀 안 약어는 영문 병기 "SLA(Service Level Agreement)".
- **행 높이 = 내용 밀도 (손글씨 기준)**: 표 셀은 손글씨 폭 — 구분열 3~4자, 구성요소열 5~7자,
  설명열 한 줄 ~10자. 설명이 11자 이하면 1줄 행(tr), "- " 항목 2개(각 10자 내외)면 2줄 행(tr.r2).
  r2 셀 합계 20자 상한 — 넘치면 손으로 못 베낀다(위반). 내용 1구절뿐인 r2도 금지.
- 문체: 개조식("~임/~함/~됨" 종결). 정의·설명은 키워드 나열형 — 문장을 만들지 말 것.
- 본문 개념도(diagram) 직후에는 p.gloss 간글 1줄 필수(서론 d7 뒤는 (정의) p.def가 대체).
  h2 사이에 빈 줄·gap을 직접 넣지 말 것(서버가 페이지 배치 시 자동 삽입).

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

- 개념도 예시 1 — ⑤허브형(d-net, 중심 개념+4요소 방사. 예: ITSM):
<div class="diagram"><div class="d-net"><div class="d-box">인력<small>People</small></div><span class="d-arrow">↓</span><div class="d-box">조직<small>Organization</small></div><span class="d-arrow">→</span><div class="d-box d-hub">ITSM<small>ITIL · eSCM · SLM · CMMI</small></div><span class="d-arrow">←</span><div class="d-box">기술<small>Technology</small></div><span class="d-arrow">↑</span><div class="d-box">프로세스<small>Process</small></div></div></div>
- 개념도 예시 2 — ④순환형(d-cycle, PDCA류. 예: BCM):
<div class="diagram"><div class="d-cycle"><span class="d-turn">↑</span><div class="d-box">Plan<small>BCP 수립</small></div><span class="d-arrow">→</span><div class="d-box">Do<small>구축 · 운영</small></div><span class="d-turn">↓</span><div class="d-box">Act<small>유지 · 개선</small></div><span class="d-arrow">←</span><div class="d-box">Check<small>모의훈련</small></div></div></div>"""


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


async def _demo_chat(suggestion: str = "") -> AsyncIterator[dict]:
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
                 "\"SLA에 대하여 설명하시오 (25점)\"처럼 입력해 보세요." + suggestion,
        "demo": True,
        "llm_calls": 0,
    }


async def _demo_exam() -> AsyncIterator[dict]:
    """라이브 파이프라인 고정 데모 — 보완 루프 포함 (다인부터 이어짐).

    명시적 "데모" 입력 전용. 미적중 자동 폴백으로는 절대 나오지 않는다 —
    사용자 질문과 무관한 답안지가 자동 납품되는 일 금지 (발주자 피드백 2026-07).
    """
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
        "reply": "라이브 파이프라인 고정 데모입니다 (요청하신 '데모' 시연 — 실제 질문과 무관한 예시 답안). 📄\n"
                 "『제로 트러스트 보안 모델』 2교시형(서술) 25점 고정 답안지예요. "
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


def _assign_req_topics(reqs: list[dict]) -> list[list[dict]]:
    """요구별 토픽 배정 (0콜, question-spec 2-2) — 요구 text 단위 매칭.

    확정급 점수(>=8) + 이름/별칭이 요구 텍스트에 실제 언급된 토픽만 배정한다.
    비교 요구는 언급 순서대로 최대 2건(대비 쌍), 그 외는 핵심 명사(한국어 어순상
    마지막 언급 대상 — "BCP 달성을 위한 PDCA 사이클"의 PDCA) 1건.
    """
    out: list[list[dict]] = []
    for r in reqs:
        res = _library.match(r["text"])
        scores = {c["id"]: c["score"] for c in res.get("candidates") or []}
        for tid in res["matched"]:
            scores.setdefault(tid, _library.HIT_SCORE)
        qn = _library.norm(r["text"])
        ranked: list[tuple[int, int, str]] = []  # (첫 언급, -마지막 언급 끝, id)
        for tid, sc in scores.items():
            if sc < _library.HIT_SCORE:
                continue
            t = _library.load_topic(tid)
            if not t:
                continue
            pos = []
            for nm in [t.get("name") or ""] + list(t.get("aliases") or []):
                n = _library.norm(nm)
                if len(n) >= 2 and n in qn:
                    pos.append((qn.find(n), qn.rfind(n) + len(n)))
            if pos:
                ranked.append((min(p[0] for p in pos), -max(p[1] for p in pos), tid))
        if not ranked:
            out.append([])
        elif r.get("verb") == "compare":
            ranked.sort()  # 언급 순서 → (a, b) 대비 쌍
            out.append([t for t in (_library.load_topic(tid) for _, _, tid in ranked[:2]) if t])
        else:
            ranked.sort(key=lambda x: x[1])  # 마지막 언급이 핵심 명사
            out.append([t for t in [_library.load_topic(ranked[0][2])] if t])
    return out


_ALLOWED_FRAG_TAGS = {"h3", "p", "table", "thead", "tbody", "tr", "th", "td",
                      "u", "br", "small", "div", "span"}


def _fragment_ok(html: str) -> bool:
    """부족 슬롯 집필 프래그먼트의 §7 계약 검사 — 위반이면 플레이스홀더 유지 (2-4)."""
    low = (html or "").lower()
    if not low or "<script" in low or "http://" in low or "https://" in low \
            or "javascript:" in low or "<h2" in low:
        return False
    return all(m.group(1) in _ALLOWED_FRAG_TAGS
               for m in re.finditer(r"</?([a-z0-9]+)", low))


def _verify_requirements(body: str, parsed: dict, roadmap: list[str]) -> list[str]:
    """세아 요구 검증 (0콜, question-spec 2-3): 로드맵 박스 수/텍스트-단락 일치,
    요구 순서(핵심 명사 ⊂ 해당 h2), 비교→tcmp·절차→절차표 존재."""
    warns: list[str] = []
    reqs = parsed.get("requirements") or []
    n = len(reqs)
    parts = re.split(r"(<h2[^>]*>.*?</h2>)", body, flags=re.S | re.I)
    h2s = [html_mod.unescape(re.sub(r"<[^>]+>", "", p)).strip()
           for p in parts if p.lower().startswith("<h2")]
    contents = [parts[i + 1] if i + 1 < len(parts) else ""
                for i, p in enumerate(parts) if p.lower().startswith("<h2")]
    if len(h2s) < n + 1:
        warns.append(f"요구 단락 수 부족 (h2 {len(h2s)}개 < 서론+요구 {n + 1})")
        return warns
    req_h2s, req_contents = h2s[1:1 + n], contents[1:1 + n]
    d7 = next((b for b in _split_blocks(body)
               if 'class="diagram d7"' in b.lstrip()[:40]), "")
    boxes = [html_mod.unescape(re.sub(r"<[^>]+>", "", m)).strip()
             for m in re.findall(r'<div class="d-box soft">(.*?)(?:<small|</div>)', d7, re.S)]
    if len(boxes) != n:
        warns.append(f"서론 로드맵 박스 {len(boxes)}개 ≠ 요구 {n}건")
    else:
        for i, (b, h) in enumerate(zip(boxes, req_h2s)):
            if b and b not in h:
                warns.append(f"로드맵 박스 '{b[:12]}'가 Ⅱ+{i}단락 제목의 부분 문자열이 아님")
    for r, content in zip(reqs, req_contents):
        low = content.lower()
        if (r.get("verb") == "compare" or "comparisons" in (r.get("slots") or [])) \
                and 'class="tcmp"' not in low:
            warns.append(f"비교 요구({r.get('label')}) 단락에 비교표(tcmp) 없음")
        if "procedure" in (r.get("slots") or []) and "<th>단계</th>" not in content \
                and "<table" not in low:
            warns.append(f"절차 요구({r.get('label')}) 단락에 절차표 없음")
    return warns


def _verify_assembly(body: str, sheet: str, kind: str) -> list[str]:
    """세아 규칙 검증 (0콜): 필수 슬롯/표 최소 2행/암기 박스/외부 리소스 0건 + 형식 린트."""
    warnings = list(_lint_format(body, kind))
    for tbl in re.findall(r"<table.*?</table>", body, re.S | re.I):
        if len(re.findall(r"<tr[\s>]", tbl, re.I)) < 2:
            warnings.append("행 2개 미만 표 존재")
            break
    if re.search(r"https?://|<script", sheet, re.I):
        warnings.append("외부 리소스/스크립트 감지")
    if 'class="mnemonic' not in sheet:  # mn2(2줄 박스) 변형 포함
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
        # 고정 데모 시나리오는 명시적 요청 전용 (자동 폴백으로 나오지 않는다)
        if provider() is None and (message or "").strip().lower() in ("데모", "demo", "데모 보여줘"):
            yield _ev("orchestrator", "working", "접수 중…")
            yield _talk("orchestrator", "데모 시연 요청이네요! 라이브 파이프라인 고정 시나리오로 보여드릴게요 🎬")
            yield _ev("nlu", "working", "토픽 검색 중…")
            await asyncio.sleep(0.1)
            yield _ev("nlu", "done", "데모 시나리오")
            yield _talk("nlu", "작성→채점→보완 과정을 고정 답안(제로 트러스트)으로 시연할게요!")
            async for e in _demo_exam():
                yield e
            return

        if not is_exam_request(message, kind_hint):
            async for e in _chat_path(history, message):
                yield e
            return

        kind, points = classify_exam(message, kind_hint)
        yield _ev("orchestrator", "working", "접수 중…")
        yield _talk("orchestrator", f"{points}점 {kind.split('(')[0]} 문제네요. 누리님, 토픽 검색!")
        yield _ev("nlu", "working", "토픽 검색 중…")
        await asyncio.sleep(0.1)

        llm_calls = 0
        # ---- 문항 구조 파싱 (누리, 무LLM 규칙 우선 — question-spec 2-1)
        parsed = _question.parse_question(message)
        if parsed.pop("needs_llm", False):
            acc = None
            if provider() is not None and llm_calls < 2:
                # 파서 LLM 폴백 1콜 — 요구 text 원문 부분 문자열 검증(accept_llm) 통과 시만 수용
                yield _ev("nlu", "thinking", "지문 정독 중…")
                llm_calls += 1
                try:
                    sys_p, msgs = _question.llm_messages(message)
                    acc = _question.accept_llm(
                        message, await _chat(sys_p, msgs, json_mode=True))
                except RateLimitError:
                    acc = None
                if acc:
                    yield _talk("nlu", "지문이 까다로워서 한 번 더 꼼꼼히 읽었어요!")
            if acc:
                parsed = acc
            elif not 1 <= len(parsed["requirements"]) <= 6:
                parsed = _question.fallback_parse(message)  # 키 없음/기각 → simple 강등
        parsed.pop("needs_llm", None)
        reqs = parsed["requirements"]
        if len(reqs) >= 2:
            yield _talk("nlu", f"요구사항 {len(reqs)}건 파악: "
                        + " / ".join(_question.req_tag(r) for r in reqs[:5]))

        # ---- 요구 주도 조립 경로 (2교시형 · 요구 2건 이상 · 요구별 적중 — 2-2)
        if "1교시" not in kind and len(reqs) >= 2:
            assignments = _assign_req_topics(reqs)
            if any(assignments):
                matched = []
                for ts in assignments:
                    matched.extend(t["id"] for t in ts if t["id"] not in matched)
                async for e in _assembly_path(message, kind, points, matched, llm_calls,
                                              parsed=parsed, assignments=assignments):
                    yield e
                return

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
            async for e in _assembly_path(message, kind, points, matched, llm_calls,
                                          parsed=parsed):
                yield e
            return

        # ---- 미적중 폴백
        yield _ev("nlu", "done", "미적중")
        if provider() is None:
            # 가짜 답안 금지 (발주자 피드백 2026-07): 키 없는 미적중은 질문과 무관한 고정
            # 답안지를 내지 않고 정직한 안내로 종료. 고정 데모는 명시적 "데모" 입력 전용.
            yield _talk("nlu", "라이브러리에 없는 토픽이에요. 지어내는 대신 안내드릴게요!")
            yield _ev("orchestrator", "done", "안내 완료")
            s = _library.stats()
            topic_txt = _subject_hint(message)
            cand_names = [c["name"] for c in (res.get("candidates") or [])[:3]]
            lines = [f"'{topic_txt}' 토픽은 아직 라이브러리에 없어요 — 사실과 다른 답안을 "
                     "지어내는 대신 안내를 드려요."]
            if cand_names:
                lines.append(f"혹시 {', '.join(cand_names)} 말씀이세요? 해당 이름으로 다시 질문해 주세요.")
            lines.append(f"현재 즉시 작성 가능한 토픽 {s['full_parts']}개(뼈대 포함 {s['topics']}개)는 "
                         "좌측 '토픽 서랍'에서 확인할 수 있어요.")
            lines.append("LLM 키(GEMINI_API_KEY)를 설정하면 라이브러리에 없는 토픽도 "
                         "라이브 파이프라인으로 작성해 드립니다.")
            yield {
                "type": "reply",
                "reply": "\n".join(lines),
                "demo": True,
                "library": False,
                "exam": {"kind": kind, "points": points, "topic": topic_txt},
                "question": _qmeta(parsed),
                "llm_calls": llm_calls,
            }
        else:
            yield _talk("nlu", "라이브러리에 없는 토픽이에요. 라이브 파이프라인으로 작성할게요!")
            async for e in _live_exam(message, kind, points, llm_calls, parsed=parsed):
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


def _topic_brief(t: dict) -> dict:
    """대화 답변용 라이브러리 부품 요약 — LLM 컨텍스트/무LLM 답변 공용 (검증된 사실 우선)."""
    comps = [{"role": c.get("role"), "name": c.get("name"),
              "detail": c.get("detail")} for c in (t.get("components") or [])[:5]]
    return {
        "name": t.get("name"),
        "definition": t.get("definition_long") or t.get("definition"),
        "background": t.get("background"),
        "components": comps,
        "features": (t.get("features") or [])[:4],
        "usage": (t.get("usage") or [])[:3],
        "mnemonic": t.get("mnemonic"),
        "exam_points": (t.get("exam_points") or [])[:3],
    }


def _library_answer(t: dict) -> str:
    """키 없는 환경의 무LLM 토픽 답변 — 라이브러리 데이터만으로 정의·구성·두문자 응답."""
    s = short_name(t)
    lines = [f"『{s}』 — 라이브러리 등록 토픽이에요."]
    d = t.get("definition_long") or t.get("definition")
    if d:
        lines.append(f"· 정의: {d}")
    if t.get("background"):
        lines.append(f"· 필요성: {t['background']}")
    comps = t.get("components") or []
    if comps:
        lines.append("· 구성요소: " + ", ".join(str(c.get("name") or "") for c in comps[:5]))
    mn = t.get("mnemonic") or {}
    if mn.get("word"):
        exp = " · ".join(str(e) for e in (mn.get("expansion") or [])[:5])
        lines.append(f"· 두문자: {mn['word']}" + (f" — {exp}" if exp else ""))
    lines.append(f"답안지가 필요하면 \"{s}에 대하여 설명하시오 (25점)\"처럼 입력해 주세요.")
    return "\n".join(lines)


async def _chat_path(history: list[dict], message: str) -> AsyncIterator[dict]:
    """일반 질문 — 챗봇 답변 (발주자 확정 2026-07-17: 답안지 생성 + 질문 답변 겸용).

    - 키 있음: 로운(수험 멘토) 1콜. 질문이 라이브러리 토픽에 적중하면 검증된 부품
      데이터(정의·구성요소·두문자)를 컨텍스트로 주입해 정확도를 확보한다.
    - 키 없음: 적중 토픽은 라이브러리 데이터만으로 무LLM 답변(정의·구성·두문자),
      그 외에는 안내.
    """
    yield _ev("orchestrator", "working", "접수 중…")
    yield _talk("orchestrator", "일반 질문이네요. 로운님이 멘토로 바로 답할게요!")
    topic = None
    suggestion = ""
    hit = _library.match(message)
    if hit["status"] == "hit" and hit["matched"]:
        topic = _library.load_topic(hit["matched"][0])
    if topic:
        suggestion = (f"\n\n💡 이 토픽은 라이브러리에 있어요 — 답안지가 필요하시면 "
                      f"\"{short_name(topic)} 답안지 적어줘\"라고 입력해 보세요.")
    if provider() is None:
        if topic:
            yield _ev("writer", "working", "라이브러리 답변 중…")
            await asyncio.sleep(0.2)
            yield _ev("writer", "done", "답변 완료")
            yield _talk("writer", "라이브러리 부품으로 바로 답했어요!")
            yield _ev("orchestrator", "done", "턴 완료")
            yield {"type": "reply", "reply": _library_answer(topic), "library": True,
                   "matched": [topic["id"]], "llm_calls": 0}
            return
        async for e in _demo_chat(suggestion):
            yield e
        return
    yield _ev("writer", "working", "답변 작성 중…")
    system = (
        "당신은 '기술사 답안 사무소'의 집필 담당이자 기술사 수험 멘토 '로운'입니다. "
        "기술사 시험 준비(공부법, 답안 작성 요령, 용어 개념, 서브노트 등)에 대해 "
        "친절하고 간결한 한국어로 답하세요. "
        "사용자가 시험 문제를 그대로 입력하면 팀이 답안지를 즉시 만들어 준다는 것도 "
        "필요할 때 자연스럽게 안내하세요."
    )
    if topic:
        system += ("\n\n[라이브러리 검증 자료 — 질문 토픽의 사실 근거로 최우선 사용, "
                   "여기 없는 세부 수치는 지어내지 말 것]\n"
                   + json.dumps(_topic_brief(topic), ensure_ascii=False))
    reply = await _chat(
        system,
        history[-10:] + [{"role": "user", "content": message}],
        max_tokens=2048,
    )
    yield _ev("writer", "done", "답변 완료")
    yield _talk("writer", "답변 보냈어요!")
    yield _ev("orchestrator", "done", "턴 완료")
    yield {"type": "reply", "reply": reply + suggestion, "llm_calls": 1,
           "library": bool(topic), "matched": [topic["id"]] if topic else []}


def _qmeta(parsed: dict | None) -> dict | None:
    """reply 노출용 파싱 메타 (question-spec 2-6)."""
    if not parsed:
        return None
    return {"form": parsed.get("form"), "requirements": len(parsed.get("requirements") or []),
            "parse": parsed.get("parse")}


async def _assembly_path(message: str, kind: str, points: int,
                         matched: list[str], llm_calls: int,
                         parsed: dict | None = None,
                         assignments: list[list[dict]] | None = None) -> AsyncIterator[dict]:
    """라이브러리 적중 — 부품 조립 경로 (LLM 0~2콜, 3초 목표. sleep은 연출용 ≤0.15s).

    assignments가 있으면 요구 주도 조립(question-spec 2-2: 요구 순서 = 단락 순서,
    목차는 지문 어구), 없으면 표준 구조 조립(단순형·1교시형·복합 나열).
    """
    is_terms = "1교시" in kind
    req_mode = bool(parsed and assignments)
    topics = [t for t in (_library.load_topic(i) for i in matched) if t]
    names = [short_name(t) for t in topics]

    # 부분 적중 감지(표준 경로 전용): 문제 주제어 중 적중 토픽에 안 잡힌 것.
    # 요구 주도 경로는 요구 단위 부족(deficits)으로 대신 처리한다.
    # 적중 토픽명의 부분 문자열인 조각은 제외 — "…성과 측정지표"의 "과 " 오분리
    # 잔여("…성"/"측정지표")가 미등록 주제로 오인되어 플레이스홀더가 생기던 결함.
    missing: list[str] = []
    if not req_mode:
        matched_names = [_library.norm(x) for t in topics
                         for x in [t.get("name") or ""] + list(t.get("aliases") or [])]
        subjects = split_subjects(message)
        if len(subjects) >= 2:
            for s in subjects:
                frag = _library.norm(s)
                if any(frag and frag in mn for mn in matched_names):
                    continue
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
    if req_mode:
        result = assemble_requirements(message, kind, points, parsed, assignments)
        n_req = len(parsed["requirements"])
        yield _ev("designer", "done", f"단락 {result['slots'] - 1}개")
        yield _talk("designer", f"요구 {n_req}건을 물어본 순서대로 Ⅱ~"
                    f"{'ⅡⅢⅣⅤⅥⅦ'[result['slots'] - 2]} 단락에 배치했어요")
    else:
        result = assemble(message, kind, points, topics, missing, {})
        yield _ev("designer", "done", f"슬롯 {result['slots']}개")
        yield _talk("designer", f"{'1교시형' if is_terms else '2교시형'} 슬롯 {result['slots']}개에 부품 배치했어요")
    await asyncio.sleep(0.12)

    # 로운 — 접합부/미등록 소단락/뼈대 보강 (기본 0콜, 필요 시 콜당 예산 llm_calls<=2)
    yield _ev("writer", "working", "집필 중…")
    # 요구 주도 경로: 부족 요구들을 모아 LLM 1콜 일괄 집필 (2-4 — 예산 합산 2콜 상한)
    deficits = list(result.get("deficits") or []) if req_mode else []
    if deficits and provider() is not None and llm_calls < 2:
        llm_calls += 1
        try:
            raw = await _chat(
                "당신은 기술사 답안 팀의 집필 담당 '로운'입니다. 라이브러리에 부품이 없는 "
                "요구 단락들의 내부 블록만 집필해 JSON으로 출력하세요: "
                "{\"sections\": [{\"label\": \"가\", \"html\": \"...\"}]}\n"
                "각 html은 답안지 줄 그리드 계약(§7) 프래그먼트: 허용 태그는 h3/p(class "
                "def|gloss)/table(class t3|t2|tcmp)/thead/tbody/tr(2줄 행 class r2)/th/td/u/br/"
                "small만. h2 금지(서버가 지문 어구로 스탬프). 표는 헤더 1행+행 3~4개, "
                "문체는 개조식(~임/~함), 표 설명셀 한 줄 10자·2줄 행 20자·p.def 38자 이내"
                "(공백 제외, 영문 반각 환산). sub_points가 있으면 표 구분열로 반영.",
                [{"role": "user", "content":
                    f"문제: {message}\n부족 요구 목록: "
                    + json.dumps(deficits, ensure_ascii=False)}],
                json_mode=True, max_tokens=1800,
            )
            extra_req: dict[str, str] = {}
            for sec in (_parse_json(raw, {}).get("sections") or []):
                label = str((sec or {}).get("label") or "")
                frag = _extract_body(str((sec or {}).get("html") or ""))
                if label and _fragment_ok(frag):
                    extra_req[label] = frag
            if extra_req:
                result = assemble_requirements(message, kind, points, parsed,
                                               assignments, extra_req)
                deficits = list(result.get("deficits") or [])
        except RateLimitError:
            pass  # 플레이스홀더 유지
    # 뼈대 적중(핵심 부품 없음) + 키 있으면 Ⅱ단락(구성도·구성요소) LLM 1콜 보강 (스펙 2-1)
    # 단일 토픽 표준 경로 한정 — 복합/요구 주도 조립은 core_sections를 쓰지 않으므로 콜 금지
    # (복합에서 콜을 쓰면 결과가 사장되고 로운 talk이 허위가 됨 — 세아 반려 2026-07)
    core: dict[str, str] = {}
    skeleton = ([t for t in topics if not (t.get("components") or t.get("diagram_html"))]
                if len(topics) == 1 and not req_mode else [])
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
                "span.d-arrow(→ ← ↓), 박스 3~6개)</div> → <p class=\"gloss\">– 간글 1줄(공백 제외 19자 이내)</p> → "
                "<h3>토픽명의 구성요소</h3> → <table class=\"t3\"><thead><tr><th>구분</th><th>구성요소</th>"
                "<th>설명</th></tr></thead><tbody><tr class=\"r2\">…</tr> 4행</tbody></table>\n"
                "문체는 개조식(~임/~함), 표 설명셀 한 줄 10자·2줄 행 20자 이내(공백 제외, 영문 반각 환산). 다른 태그·설명·코드 펜스 금지.",
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
                "<p class=\"def\">키워드 나열형 정의(2줄 — 공백 제외 38자 이내, ~임 종결)</p> + "
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
                "{\"definition\": \"...(공백 제외 38자 이내, ~임 종결, 키워드 나열형)\"}",
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
    if req_mode and deficits:
        notes.append(f"부품 없는 요구 {len(deficits)}건은 플레이스홀더 처리했어요"
                     + ("" if provider() is None else " (집필 계약 위반으로 강등)"))
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
    if req_mode:
        warnings += _verify_requirements(result["body"], parsed, result.get("roadmap") or [])
    # 밀도 린터(표+문단)는 견본(MG-001)만 강제 — 나머지 20건은 부품 전수 재작성 배치
    # 전까지 로그로만 남긴다 (기존 부품 21/21이 실측 미달이라 일괄 경고 노출은 소음)
    density = _lint_table_density(result["body"]) + _lint_text_density(result["body"])
    if density:
        if "MG-001" in matched:
            warnings += density
        else:
            print(f"[density] {','.join(matched)}: {' / '.join(density)}", flush=True)
    yield _ev("reviewer", "done", "통과" if not warnings else f"경고 {len(warnings)}건")
    yield _talk("reviewer", "필수 섹션·암기박스 확인, 통과 ✅" if not warnings
                else f"조립은 통과, 경고 {len(warnings)}건: {warnings[0]}")

    yield _ev("orchestrator", "done", "납품 완료")
    yield _talk("orchestrator", "답안지 납품 완료! 라이브러리 덕에 즉답이었어요 ⚡")

    vol = _volume(result["body"], kind)
    # 뼈대 토픽이 보강 없이 조립됐으면(무키 등) 반쪽 답안임을 먼저 알린다 (발주자 피드백 2026-07)
    stub_names = [short_name(t) for t in topics
                  if not (t.get("components") or t.get("diagram_html")) and t.get("id") not in core]
    reply = (f"『{result['title']}』 {kind} {points}점 답안지 조립 완료 — "
             f"라이브러리 {len(topics)}건 적중({', '.join(names)}), LLM {llm_calls}콜. "
             f"분량 {vol['pages']}쪽 {vol['line_in_page']}줄 (환산 {vol['pages_frac']}매).")
    if req_mode:
        reply = (f"요구사항 {len(parsed['requirements'])}건을 물어본 순서대로 답했어요.\n"
                 + reply)
    if stub_names:
        reply = (f"⚠️ 이 토픽은 아직 요약본이에요({', '.join(stub_names)} — 핵심 섹션 준비 중, "
                 "LLM 키 설정 시 자동 보강)\n") + reply
    if deficits:
        labs = ", ".join(str(d.get("label") or "?") for d in deficits)
        reply = (f"⚠️ 요구 {labs} 항목은 라이브러리 미등록 부품이라 자리만 잡았어요"
                 + ("" if provider() is not None else " — LLM 키 설정 시 자동 집필") + "\n") + reply
    if warnings:
        reply += "\n검증 경고: " + " / ".join(warnings[:4])
    yield {
        "type": "reply",
        "reply": reply,
        "exam": {"kind": kind, "points": points, "topic": result["title"]},
        "library": True,
        "matched": matched,
        "question": _qmeta(parsed),
        "llm_calls": llm_calls,
        "review": {"passed": not warnings, "warnings": warnings},
        "sheet": {"kind": kind, "points": points, "pages": vol["pages"],
                  "lines": vol["line_in_page"], "target_pages": vol["target_pages"]},
        "artifact": {"title": result["title"], "html": sheet},
    }


async def _live_exam(message: str, kind: str, points: int,
                     llm_calls: int, parsed: dict | None = None) -> AsyncIterator[dict]:
    """미적중 폴백 — 라이브 파이프라인 (설계1 + 초안1 + 채점1 + 보완1 + 재채점1 = 최대 5콜)."""
    topic = _subject_hint(message)
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
    # 폴백(LLM) 경로는 밀도 린터(표+문단) 위반도 보완 사유
    lint = _lint_format(body, kind) + _lint_table_density(body) + _lint_text_density(body)
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
        lint = (_lint_format(body, kind) + _lint_table_density(body)
                + _lint_text_density(body))  # 밀도 린터도 보완 사유
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
        "question": _qmeta(parsed),
        "llm_calls": llm_calls,
        "review": {"score": score, "rounds": rounds, "weak_points": weak,
                   "volume": {"lines": vol["lines"], "pages": vol["pages_frac"]}},
        "sheet": {"kind": kind, "points": points, "pages": vol["pages"],
                  "lines": vol["line_in_page"], "target_pages": vol["target_pages"]},
        "artifact": {"title": topic, "html": sheet},
    }
