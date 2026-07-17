"""2교시 문항 구조 파서 — 무LLM 규칙 우선 (docs/question-spec.md 2-1).

문항 텍스트를 요구사항 리스트로 파싱한다. 출력 스키마(조립기·집필·검증 공유 계약):

{
  "form": "sub | inline | simple",
  "scenario": "리드 지문(있으면, 원문 그대로)",
  "requirements": [{"label", "text", "verb", "objects", "slots", "sub_points", "count"}],
  "parse": "rule | llm | fallback",
}

- 요구 text는 항상 **원문 부분 문자열**(공백 정규화 기준) — 목차 스탬프·검증 계약.
- LLM 호출은 이 모듈에 없다. 규칙 파싱이 부적합하면 needs_llm()이 True를 주고,
  _agents가 예산 내에서 llm_messages()로 1콜 후 accept_llm()으로 검증 수용한다.
  키가 없으면 fallback_parse()(simple 1요구, parse=fallback)로 강등한다.
"""

from __future__ import annotations

import json
import re

# ---------------------------------------------------------------- 트리거 사전
# 요구 대상 명사의 트리거가 동사보다 우선한다 (question-spec 2-2 표).
# (트리거 정규식, verb, 1순위 슬롯) — 텍스트 등장 순서대로 슬롯을 수집한다.
_TRIGGERS: list[tuple[str, str, str]] = [
    (r"비교|차이점|차이|vs", "compare", "comparisons"),
    (r"절차|과정|단계|프로세스|공정", "explain", "procedure"),
    (r"구성도|아키텍처|개념도|메커니즘|동작\s*원리", "explain", "diagram"),
    (r"구성\s*요소|기술\s*요소|요소\s*기술|기능|유형|종류|주요\s*기준|평가\s*기준", "explain", "components"),
    (r"배경|필요성|중요성|이유|목적", "reason", "background"),
    (r"방안|방향|활용|기대\s*효과|전망|방법(?!론)", "propose", "usage"),
    (r"특징|장단점|장점|단점|한계|문제점|쟁점|시사점|제약", "explain", "features"),
    (r"사례|경험|적용", "apply", "usage"),
    (r"개념|정의|개요|역할|내용", "define", "definition_long"),
]
_TRIGGER_RES = [(re.compile(p), v, s) for p, v, s in _TRIGGERS]

# 요구 종결(동사구) 검출 — 명사구 판정·꼬리 제거에 사용
_ASK_VERBS = "설명|기술|서술|논술|약술|제시|비교|분석|정의|도출|수립|작성|논"
_TAIL_RE = re.compile(
    rf"\s*(?:에\s*대하여|에\s*대해|에\s*관하여)?\s*(?:각각|상세히|구체적으로)?\s*"
    rf"(?:(?:{_ASK_VERBS})\s*)?하(?:시오|라|세요|여라)\s*[.?!]?\s*$")
_SENT_END_RE = re.compile(r"(?:하시오|하라|하세요|하여라|할\s*것|인가|는가)\s*[.?!]?\s*$")
_POINTS_RE = re.compile(r"\(\s*\d{1,3}\s*점\s*\)")

# 복동사 분해 — 보수적 연결어미 패턴만 (질문-스펙 규칙 3, 과분해 방지 최대 2분할)
_COMPOUND_RE = re.compile(rf"((?:{_ASK_VERBS}))(?:하고|한\s*후)[,\s]+")

# 마커 패밀리 (규칙 1) — 순차 소비로 오탐 차단 ("절차 다."의 '다'는 나. 뒤에서만 마커)
_MARKER_FAMILIES: list[list[str]] = [
    list("가나다라마바사아"),
    [str(i) for i in range(1, 9)],
    list("①②③④⑤⑥⑦⑧"),
]


def _marker_re(mark: str) -> re.Pattern:
    if mark in "①②③④⑤⑥⑦⑧":
        return re.compile(rf"\s*{mark}\s*")
    if mark.isdigit():
        return re.compile(rf"(?<!\S){mark}[).]\s*")
    return re.compile(rf"(?<!\S){mark}\.\s*")


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _strip_parens(s: str) -> str:
    return re.sub(r"\([^)]*\)", " ", s or "")


def _is_noun_phrase(s: str) -> bool:
    """종결어미 없는 20자 이하 명사구인가 (규칙 2 판정 — 괄호 제거 후)."""
    core = _squash(_strip_parens(s))
    return bool(core) and len(core) <= 20 and not _SENT_END_RE.search(core) \
        and not _COMPOUND_RE.search(core)


def _find_triggers(text: str) -> list[tuple[str, str]]:
    """텍스트에서 (verb, slot)를 등장 순서로 수집 (중복 슬롯 제거)."""
    found: list[tuple[int, str, str]] = []
    for pat, verb, slot in _TRIGGER_RES:
        m = pat.search(text)
        if m:
            found.append((m.start(), verb, slot))
    found.sort()
    out: list[tuple[str, str]] = []
    for _, verb, slot in found:
        if slot not in [s for _, s in out]:
            out.append((verb, slot))
    return out[:3]


def _sub_points(text: str) -> list[str]:
    """괄호 안 콤마 나열 → sub_points (규칙 5). 영문 병기·개수 지정 괄호는 제외."""
    for m in re.finditer(r"\(([^)]{2,60})\)", text):
        inner = m.group(1)
        if re.search(r"\d+\s*(?:가지|개)", inner):
            continue
        if not re.search(r"[가-힣]", inner) or not re.search(r"[,·]", inner):
            continue
        items = []
        for it in re.split(r"[,·、]", inner):  # "/"는 병기("책임/목표")라 분리 안 함
            it = re.sub(r"\s*(?:측면|관점|중심|기준)?\s*(?:등)?\s*$", "", it.strip())
            if it:
                items.append(it)
        if len(items) >= 2:
            return items[:5]
    return []


def _count_of(text: str) -> int:
    m = re.search(r"(\d+)\s*가지|(\d+)\s*개\s*이상", text)
    return int(m.group(1) or m.group(2)) if m else 0


def _clean_req_text(s: str) -> str:
    """요구 text — 원문 부분 문자열 보존을 위해 앞뒤 공백·구두점만 다듬는다."""
    return _squash(s).strip(" .,;:")


def _objects_of(text: str) -> list[str]:
    """요구 대상 명사구 1~3개 (베스트에포트 — 매칭·집필 힌트용)."""
    core = _squash(_strip_parens(_TAIL_RE.sub(" ", text)))
    core = re.sub(r"(?:을|를|이|가|은|는)\s*$", "", core).strip()
    parts = [p.strip() for p in re.split(r"\s*(?:,|·|및)\s*", core) if len(p.strip()) >= 2]
    return parts[:3] or ([core] if core else [])


def _make_req(label: str, text: str, lead_triggers: list[tuple[str, str]],
              tail_verb: str | None = None) -> dict:
    text = _clean_req_text(text)
    trig = _find_triggers(_strip_parens(text)) or lead_triggers
    slots = [s for _, s in trig] or ["components"]
    if any(v == "compare" for v, _ in trig):
        verb = "compare"
    elif any(v == "apply" for v, _ in trig):
        verb = "apply"
    elif trig:
        verb = trig[0][0]
    else:
        verb = tail_verb or "explain"
    return {
        "label": label,
        "text": text,
        "verb": verb,
        "objects": _objects_of(text),
        "slots": slots,
        "sub_points": _sub_points(text),
        "count": _count_of(text),
    }


def _split_compound(text: str) -> list[str]:
    """복동사 분해 (규칙 3) — '…하고 / …한 후' 경계, 최대 2분할(3조각)."""
    parts: list[str] = []
    rest = text
    for _ in range(2):
        m = _COMPOUND_RE.search(rest)
        if not m:
            break
        parts.append(rest[: m.end(1)])  # 동사까지 포함해 원문 부분 문자열 유지
        rest = rest[m.end():]
    parts.append(rest)
    return [p for p in (p.strip() for p in parts) if p]


def _split_markers(text: str):
    """마커 분리 (규칙 1). 반환 (lead, items) — 마커 2개 미만이면 items=None."""
    for family in _MARKER_FAMILIES:
        m0 = _marker_re(family[0]).search(text)
        if not m0:
            continue
        spans = [(m0.start(), m0.end())]
        pos = m0.end()
        for mark in family[1:]:
            mm = _marker_re(mark).search(text, pos)
            if not mm:
                break
            spans.append((mm.start(), mm.end()))
            pos = mm.end()
        if len(spans) >= 2:
            lead = text[: spans[0][0]]
            items = [text[spans[i][1]: spans[i + 1][0]] for i in range(len(spans) - 1)]
            items.append(text[spans[-1][1]:])
            labels = family[: len(items)]
            return lead, list(zip(labels, items))
    return text, None


def _split_scenario(text: str) -> tuple[str, str]:
    """선언형 리드 문장(시나리오)과 요구 문장을 분리한다 (수식 속성 — 스펙 0장)."""
    sents = re.split(r"(?<=[.?!])\s+", text.strip())
    scen: list[str] = []
    ask: list[str] = []
    for s in sents:
        if ask or re.search(r"하시오|하라|하세요|다음|아래|물음", s):
            ask.append(s)
        elif re.search(r"(?:다|이다|있다|한다|된다|이며|음)\s*[.]?\s*$", s.strip()):
            scen.append(s)
        else:
            ask.append(s)
    return _squash(" ".join(scen)), " ".join(ask).strip()


def _tail_verb(text: str) -> str | None:
    m = re.search(rf"({_ASK_VERBS})\s*하(?:시오|라|세요)", text)
    if not m:
        return None
    return {"제시": "propose", "비교": "compare", "정의": "define",
            "도출": "propose", "수립": "propose"}.get(m.group(1), "explain")


def parse_question(question: str) -> dict:
    """규칙 기반 파싱 (LLM 0콜). 반환 dict에 내부 플래그 needs_llm 포함."""
    raw = _squash(question or "")
    text = _POINTS_RE.sub(" ", raw).strip()
    scenario, ask = _split_scenario(text)
    lead, items = _split_markers(ask or text)
    reqs: list[dict] = []
    consumed: list[str] = [scenario]  # 잔여율 산정용 — 파서가 소비한 원문 조각

    if items is not None:
        # ---- 소문항형 (form=sub)
        form = "sub"
        lead_scen, lead_ask = _split_scenario(lead)
        if lead_scen:
            scenario = _squash(f"{scenario} {lead_scen}")
        lead_triggers = _find_triggers(_strip_parens(lead_ask or lead))
        lead_verb = _tail_verb(lead_ask or lead)
        consumed.append(lead)
        for label, item in items:
            item = item.strip()
            if not item:
                continue
            consumed.append(item)
            if _is_noun_phrase(item):
                # 대상(명사) 나열 — 리드의 동사·트리거 복제 (규칙 2)
                reqs.append(_make_req(label, item, lead_triggers, lead_verb))
            else:
                # 요구 문장 — 복동사 분해 (규칙 3, 최대 2분할)
                pieces = _split_compound(_TAIL_RE.sub(" ", item).strip() or item)
                for j, piece in enumerate(pieces):
                    lab = label if j == 0 else f"{label}{j + 1}"
                    reqs.append(_make_req(lab, piece, [], _tail_verb(item)))
    else:
        # ---- 마커 없음: 복동사 분해 → 인라인 나열 → 단순형 (규칙 3·4)
        # 단일 문장 전체를 소비하므로 잔여 없음
        body = ask or text
        consumed.append(body)
        pieces = _split_compound(_TAIL_RE.sub(" ", body).strip() or body)
        if len(pieces) >= 2:
            form = "inline"
            for j, piece in enumerate(pieces):
                reqs.append(_make_req(f"R{j + 1}", piece, [], _tail_verb(body)))
        else:
            inline_items = _inline_items(body)
            if inline_items:
                form = "inline"
                for j, it in enumerate(inline_items):
                    reqs.append(_make_req(f"R{j + 1}", it, [], _tail_verb(body)))
            else:
                form = "simple"
                core = _clean_req_text(_TAIL_RE.sub(" ", body)) or _clean_req_text(body)
                reqs.append(_make_req("R1", core, [], _tail_verb(body)))
                # 단순형 sub_points는 괄호가 어미 뒤에 붙는 경우가 많아 전문에서 추출
                if not reqs[-1]["sub_points"]:
                    reqs[-1]["sub_points"] = _sub_points(body)
                if not reqs[-1]["count"]:
                    reqs[-1]["count"] = _count_of(body)

    return {
        "form": form,
        "scenario": scenario,
        "requirements": reqs,
        "parse": "rule",
        "needs_llm": _needs_llm(text, consumed, reqs),
    }


def _inline_items(body: str) -> list[str] | None:
    """인라인 나열 분리 (규칙 4) — 요구 어미 앞의 콤마·'및' 나열구.

    나열 항목 전부가 명사구이고 **트리거 명사를 포함**할 때만 분리한다 —
    대상 명사 나열("센서 및 통신기술")을 요구로 오분리하지 않기 위한 보수 조건
    (표본 #14 vs #20·#22 판별).
    """
    m = re.search(rf"(.+?)(?:에\s*대하여|에\s*대해|을|를)\s*"
                  rf"(?:각각\s*)?(?:{_ASK_VERBS})\s*하(?:시오|라|세요)", body)
    head = m.group(1) if m else _TAIL_RE.sub(" ", body)
    if not re.search(r"[,·]|및", head):
        return None
    items = [it.strip() for it in re.split(r"\s*(?:,|·|및)\s*", head) if it.strip()]
    if len(items) < 2 or len(items) > 5:
        return None
    for it in items:
        if not _is_noun_phrase(it) or not _find_triggers(_strip_parens(it)):
            return None
    return items


def _needs_llm(text: str, consumed: list[str], reqs: list[dict]) -> bool:
    """LLM 폴백 필요 판정 (규칙 7): 요구 0건 / 과분해(6개 초과) / 잔여 30% 초과.

    consumed: 파서가 소비한 원문 조각(시나리오·리드·소문항 원문) — 마커·문형이
    일부만 맞아 텍스트가 흘러 나간 경우를 잔여율로 잡는다.
    """
    if not reqs or len(reqs) > 6:
        return True
    total = max(1, len(re.sub(r"\s+", "", text)))
    covered = sum(len(re.sub(r"\s+", "", c or "")) for c in consumed)
    return (total - min(covered, total)) / total > 0.3


def fallback_parse(question: str) -> dict:
    """규칙 부적합 + 키 없음 → simple 1요구 강등 (규칙 7)."""
    raw = _squash(question or "")
    text = _POINTS_RE.sub(" ", raw).strip()
    scenario, ask = _split_scenario(text)
    core = _clean_req_text(_TAIL_RE.sub(" ", ask or text)) or _clean_req_text(text)
    req = _make_req("R1", core[:60] or "문항", [], "explain")
    return {"form": "simple", "scenario": scenario, "requirements": [req],
            "parse": "fallback", "needs_llm": False}


# ---------------------------------------------------------------- LLM 폴백 지원

_LLM_SCHEMA = """{"form": "sub|inline|simple", "scenario": "리드 지문 원문 또는 빈 문자열",
 "requirements": [{"label": "가", "text": "요구 원문(문항의 부분 문자열이어야 함)",
   "verb": "explain|compare|propose|define|reason|apply",
   "objects": ["대상 명사구"], "sub_points": ["괄호 지정"], "count": 0}]}"""


def llm_messages(question: str) -> tuple[str, list[dict]]:
    """파서 LLM 폴백 1콜용 (system, messages). JSON 모드로 호출할 것."""
    system = (
        "당신은 기술사 시험 문항 구조 분석기입니다. 문항을 요구사항 리스트로 분해해 "
        "JSON만 출력하세요.\n"
        f"스키마: {_LLM_SCHEMA}\n"
        "- 각 요구 text는 반드시 문항 원문의 연속된 부분 문자열(어미 포함 가능). "
        "원문에 없는 요구를 만들지 마세요.\n"
        "- 소문항 마커(가.나./1)2)/①②)가 있으면 form=sub, 마커 없이 나열·복수 동사면 "
        "inline, 단일 요구면 simple.\n"
        "- 요구는 최대 6개, 물어본 순서 그대로.")
    return system, [{"role": "user", "content": f"문항: {question}"}]


def accept_llm(question: str, raw: str) -> dict | None:
    """LLM 파싱 결과 검증 수용 — 요구 text가 원문 부분 문자열일 때만 (규칙 7).

    위반·형식 오류면 None (호출자는 규칙 파싱 결과로 폴백).
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", raw or "", re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    form = data.get("form")
    reqs_in = data.get("requirements")
    if form not in ("sub", "inline", "simple") or not isinstance(reqs_in, list) \
            or not reqs_in or len(reqs_in) > 6:
        return None
    hay = re.sub(r"\s+", "", question or "")
    reqs: list[dict] = []
    for i, r in enumerate(reqs_in):
        if not isinstance(r, dict):
            return None
        text = _clean_req_text(str(r.get("text") or ""))
        if not text or re.sub(r"\s+", "", text) not in hay:
            return None  # 원문에 없는 요구 생성 금지 — 전체 기각
        req = _make_req(str(r.get("label") or f"R{i + 1}"), text, [])
        if r.get("verb") in ("explain", "compare", "propose", "define", "reason", "apply"):
            req["verb"] = r["verb"]
        if isinstance(r.get("sub_points"), list):
            req["sub_points"] = [str(x) for x in r["sub_points"]][:5] or req["sub_points"]
        try:
            req["count"] = max(req["count"], int(r.get("count") or 0))
        except (TypeError, ValueError):
            pass
        reqs.append(req)
    scen = str(data.get("scenario") or "")
    if scen and re.sub(r"\s+", "", scen) not in hay:
        scen = ""
    return {"form": form, "scenario": _squash(scen), "requirements": reqs,
            "parse": "llm", "needs_llm": False}


# ---------------------------------------------------------------- 표기 유틸

_SLOT_KO = {
    "comparisons": "비교", "procedure": "절차", "diagram": "구성도",
    "components": "구성요소", "background": "필요성", "usage": "방안",
    "features": "특징", "definition_long": "정의",
}


def req_tag(r: dict) -> str:
    """누리 talk용 요구 한 단어 요약 ("절차 / 비교 / 방안")."""
    if r.get("verb") == "apply":
        return "적용"
    return _SLOT_KO.get((r.get("slots") or [""])[0], "설명")


def req_title(r: dict) -> str:
    """단락(h2) 제목용 지문 어구 — 괄호·요구 어미·꼬리 조사만 벗긴 원문."""
    t = _squash(_strip_parens(r.get("text") or ""))
    t = _TAIL_RE.sub(" ", t)
    # "…을/를 설명" 꼬리만 제거 — "ISMP 비교"처럼 요구 명사가 동사와 동형이면 보존
    t = re.sub(rf"(?:을|를)\s*(?:{_ASK_VERBS})$", " ", t)
    t = re.sub(r"\s*(?:을|를|에|은|는|이|가|의)\s*$", " ", _squash(t))
    return _squash(t) or _squash(_strip_parens(r.get("text") or ""))


def clip_label(title: str, limit: int = 12) -> str:
    """로드맵 d-box 라벨 — 12자 절삭(어절 경계 우선). 항상 title의 부분 문자열."""
    t = _squash(title)
    if len(t) <= limit:
        return t
    cut = t[:limit]
    if " " in cut[3:]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ·,의및와과")
