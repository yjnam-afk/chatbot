"""토픽 부품 → 답안 본문 조립기 (LLM 0콜, docs/library-spec.md 2-4).

템플릿 슬롯(docs/answer-template-spec.md §4·§5)에 토픽 JSON 부품을 배치한다.
- 부품이 없는 선택 슬롯은 생략하고 경고만 남긴다 (조립이 깨지지 않게).
- 출력은 답안 본문 HTML 프래그먼트 — render_answer()가 답안지로 감싼다.
"""

from __future__ import annotations

import html as html_mod
import re

from _topic_library import _STOP_TOKENS, norm as _norm


def _esc(s) -> str:
    return html_mod.escape(str(s or ""))


def short_name(topic: dict) -> str:
    """표시용 짧은 이름 (괄호 제거)."""
    base = re.sub(r"\([^)]*\)", " ", topic.get("name") or "")
    base = re.sub(r"\s+", " ", base).strip()
    return base or (topic.get("name") or topic.get("id") or "")


def _emph(text, keywords=None, quote: bool = False, limit: int = 2) -> str:
    """텍스트를 escape하고 keywords와 겹치는 어절에 밑줄(<u>) 강조 (체크리스트 S2).

    방법론의 키워드 강조 = 밑줄 + "쌍따옴표"(1개). quote=True면 첫 강조어를
    쌍따옴표로도 감싼다(원문에 이미 따옴표가 있으면 생략). 긴 키워드부터 최대
    limit개, 이미 강조한 어절의 부분 문자열은 중복 강조하지 않는다.
    """
    esc = _esc(text)
    if not keywords:
        return esc
    done: list[str] = []
    for kw in sorted({str(k).strip() for k in keywords if str(k).strip()},
                     key=len, reverse=True):
        if len(done) >= limit:
            break
        k = _esc(kw)
        if len(k) < 2 or k not in esc or any(k in d for d in done):
            continue
        rep = f"<u>{k}</u>"
        if quote and not done and "&quot;" not in esc:
            rep = f'"{rep}"'
        esc = esc.replace(k, rep, 1)
        done.append(k)
    return esc


def _auto_gloss(t: dict) -> str:
    """개념도 간글 부재 시 keywords로 1줄 합성 (체크리스트 G1 — 그림 뒤 간글 필수)."""
    kws = [str(k) for k in (t.get("keywords") or []) if str(k).strip()][:3]
    core = " · ".join(kws)[:24] if kws else short_name(t)
    return f"– {core} 중심의 {short_name(t)} 동작 구조"


# ---------------------------------------------------------------- 표 빌더

def _detail_cell(detail, kws=None, compact: bool = False) -> tuple[str, int]:
    """detail(문자열 또는 개조식 항목 list) → (셀 HTML, 행 줄수 1|2).

    체크리스트 T4~T6: list는 "- 항목<br>- 항목" 개조식(2항목이면 2줄 행),
    문자열은 29자 이상이면 2줄 행. compact=True(축약 표)는 첫 항목만 1줄로.
    """
    if isinstance(detail, (list, tuple)):
        items = [re.sub(r"^[-–]\s*", "", str(x).strip()) for x in detail if str(x).strip()]
        if compact:
            detail = items[0] if items else ""
        else:
            html = "<br>".join("- " + _emph(i, kws, limit=1) for i in items[:2])
            return html, (2 if len(items) >= 2 else 1)
    s = str(detail or "")
    if compact:
        return _esc(s[:28]), 1
    return _emph(s, kws, limit=1), (2 if len(s) >= 29 else 1)


def _t3(rows: list[dict], r2: bool = True, headers=("구분", "구성요소", "설명"),
        kws=None) -> str:
    """구성요소 3단표 — 행 높이 자동 선택 + 1열 rowspan 병합 + 셀 개조식 (T3~T6).

    - 행 단위 r1/r2 자동: detail이 개조식 2항목 또는 29자 이상일 때만 2줄 행
      (환산 기준: 설명열 폭 60% ≈ 30자/줄 — "내용 1구절 r2" 반려 방지)
    - 연속 행의 role이 같으면 1열(구분)을 rowspan 병합 (실물 카테고리 병합 22~28%)
    - r2=False는 축약 모드(복합/1교시): 전행 1줄, detail은 1줄 분량으로 절삭
    """
    rows = rows[: 4 if r2 else 6]
    spans: list[int] = []  # 병합 시작 행이면 span 수, 병합 꼬리 행이면 0
    i = 0
    while i < len(rows):
        j = i + 1
        role = str(rows[i].get("role") or "")
        while j < len(rows) and role and str(rows[j].get("role") or "") == role:
            j += 1
        spans.append(j - i)
        spans.extend([0] * (j - i - 1))
        i = j
    trs = []
    for idx, r in enumerate(rows):
        cell, lines = _detail_cell(r.get("detail"), kws, compact=not r2)
        cls = ' class="r2"' if lines == 2 else ""
        if spans[idx] > 1:
            first = f'<td rowspan="{spans[idx]}">{_esc(r.get("role"))}</td>'
        elif spans[idx] == 1:
            first = f"<td>{_esc(r.get('role'))}</td>"
        else:
            first = ""  # rowspan 병합 꼬리 행
        trs.append(f"<tr{cls}>{first}<td>{_esc(r.get('name'))}</td><td>{cell}</td></tr>")
    th = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    return f'<table class="t3"><thead><tr>{th}</tr></thead><tbody>{"".join(trs)}</tbody></table>'


def _t2(rows: list[dict], headers=("구분", "설명"), kws=None) -> str:
    """2단표 — desc가 개조식 list(2항목)면 2줄 행, 문자열이면 1줄 행(열폭 80%≈38자)."""
    trs = []
    for r in rows[:4]:
        desc = r.get("desc")
        if isinstance(desc, (list, tuple)):
            items = [re.sub(r"^[-–]\s*", "", str(x).strip()) for x in desc if str(x).strip()]
            cell = "<br>".join("- " + _emph(i, kws, limit=1) for i in items[:2])
            cls = ' class="r2"' if len(items) >= 2 else ""
        else:
            cell, cls = _emph(desc, kws, limit=1), ""
        trs.append(f"<tr{cls}><td>{_esc(r.get('item'))}</td><td>{cell}</td></tr>")
    th = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    return f'<table class="t2"><thead><tr>{th}</tr></thead><tbody>{"".join(trs)}</tbody></table>'


def _tcmp(axes: list[dict], a_name: str, b_name: str) -> str:
    trs = "".join(
        f"<tr><td>{_esc(r.get('axis'))}</td><td>{_esc(r.get('a'))}</td><td>{_esc(r.get('b'))}</td></tr>"
        for r in axes[:4]
    )
    return (f'<table class="tcmp"><thead><tr><th>구분</th><th>{_esc(a_name)}</th>'
            f"<th>{_esc(b_name)}</th></tr></thead><tbody>{trs}</tbody></table>")


def _mutual_comparison(topics: list[dict]):
    """토픽 쌍의 상호 comparisons 항목 탐색 → (기준 토픽, 비교 dict) 또는 None."""
    ids = {t.get("id") for t in topics}
    for t in topics:
        for c in t.get("comparisons") or []:
            if c.get("vs") in ids and c.get("vs") != t.get("id"):
                return t, c
    return None


def _auto_compare_axes(ta: dict, tb: dict) -> list[dict]:
    """comparisons 부재 시 features를 축 정렬한 자동 대비표."""
    fa, fb = ta.get("features") or [], tb.get("features") or []
    axes = []
    for i in range(min(3, max(len(fa), len(fb)))):
        axes.append({
            "axis": (fa[i]["item"] if i < len(fa) else fb[i]["item"]),
            "a": fa[i]["desc"] if i < len(fa) else "—",
            "b": fb[i]["desc"] if i < len(fb) else "—",
        })
    if not axes:
        axes = [{"axis": "정의", "a": ta.get("definition", ""), "b": tb.get("definition", "")}]
    return axes


# ---------------------------------------------------------------- 슬롯 조립

def _slot_intro_p2(t: dict, parts: list, warnings: list) -> None:
    """2교시 Ⅰ. 서론 — intro_diagram 있으면 Type IV 로드맵, 없으면 Type I(정의+필요성)."""
    s = short_name(t)
    kws = t.get("keywords") or []
    parts.append(f"<h2>{_esc(s)}의 개요</h2>")
    if t.get("intro_diagram_html"):
        parts.append(t["intro_diagram_html"])
    if t.get("definition") or t.get("definition_long"):
        # 정의 2줄은 키워드 나열로 꽉 채움(체크리스트 S1) — 밀도 있는 definition_long
        # 우선, 강조는 밑줄+쌍따옴표(S2). 35자 definition은 폴백.
        parts.append(f'<p class="def">(정의) '
                     f'{_emph(t.get("definition_long") or t.get("definition"), kws, quote=True)}</p>')
    if t.get("background"):
        parts.append(f'<p class="def">(필요성) {_emph(t["background"], kws, limit=1)}</p>')
    elif not t.get("intro_diagram_html"):
        warnings.append("필요성(background) 부품 없음 — 서론 축약")


def _slot_structure(t: dict, parts: list, warnings: list, r2: bool,
                    core: str | None = None) -> bool:
    """Ⅱ. 구성도+구성요소. 슬롯을 하나라도 채우면 True.

    core: 뼈대 토픽(부품 없음)일 때 LLM 1콜로 보강한 Ⅱ단락 프래그먼트 (스펙 2-1 —
    "부품 부재 시 핵심 섹션은 LLM 1콜 보강"). 키 없으면 None → 슬롯 생략 + 경고.
    """
    s = short_name(t)
    has = bool(t.get("diagram_html") or t.get("components"))
    if not has:
        if core:
            parts.append(core)
            return True
        warnings.append(f"{s}: 구성도/구성요소 부품 없음 — Ⅱ단락 생략")
        return False
    parts.append(f"<h2>{_esc(s)}의 구성도 및 구성요소</h2>")
    if t.get("diagram_html"):
        parts.append(f"<h3>{_esc(s)}의 구성도</h3>")
        parts.append(t["diagram_html"])
        # 본문 개념도 직후 간글 1줄 필수 (체크리스트 G1) — 부품 부재 시 keywords로 합성
        parts.append(f'<p class="gloss">{_esc(t.get("diagram_gloss") or _auto_gloss(t))}</p>')
    if t.get("components"):
        parts.append(f"<h3>{_esc(s)}의 구성요소</h3>")
        parts.append(_t3(t["components"], r2=r2, kws=t.get("keywords")))
        if t.get("components_gloss"):
            parts.append(f'<p class="gloss">{_esc(t["components_gloss"])}</p>')
    return True


_ASK_TAIL_RE = re.compile(
    r"\s*(?:에\s*대하여|에\s*대해|을|를)?\s*(?:상세히|구체적으로|각각)?\s*"
    r"(?:설명|기술|서술|논|약술|제시|비교)?\s*하(?:시오|라|세요).*$")


def asked_phrase(question: str, names: list[str]) -> str:
    """단순형 문항에서 "물어본 지문 어구"를 추출한다 (체크리스트 M6·M12 — 단순형 범위).

    "ITSM의 구축 방안에 대하여 설명하시오" → "구축 방안": 요구 어미·배점·토픽명을
    벗겨낸 잔여 명사구를 Ⅲ단락 제목에 지문 그대로 스탬프하기 위한 것.
    잔여가 없거나(토픽명만 물음) 명사구로 부적합하면 빈 문자열 — 기본 제목 사용.
    (소문항·복수 요구 문형의 전체 파싱은 question-spec M7~M10 배치)
    """
    q = re.sub(r"\(\s*\d{1,3}\s*점\s*\)", " ", question or "")
    q = _ASK_TAIL_RE.sub(" ", q).strip()
    for n in sorted({n for n in names if n and len(n) >= 2}, key=len, reverse=True):
        if n in q:
            q = q.replace(n, " ", 1)
            break
    q = re.sub(r"\s+", " ", q).strip()
    # 토픽명 제거로 홀로 남은 조사만 제거 ("ITSM의 구축 방안"→" 의 구축 방안"→"구축 방안").
    # 단어에 붙은 "의"(의사결정 등)는 공백 경계가 아니라 보존된다.
    q = re.sub(r"^(?:의|에서|에|은|는|이|가)\s+", "", q)
    q = re.sub(r"\s+(?:의|에서|에|을|를)$", "", q).strip()
    if not (2 <= len(q) <= 20) or re.search(r"[?.!]|하시오|시오$", q):
        return ""
    if _norm(q) in _STOP_TOKENS:
        return ""
    return q


def _missing_placeholder(name: str) -> list[str]:
    """부분 적중에서 미등록 토픽 소단락 플레이스홀더 (키 없을 때)."""
    return [
        f"<h3>{_esc(name)}의 개요</h3>",
        f'<p class="def">(라이브러리 미등록 토픽) {_esc(name)}의 상세 부품이 라이브러리에 없어 '
        "요약을 생략함 — LLM 키 설정 시 자동 집필됨</p>",
    ]


def _composite_intro(topics: list[dict], joined: str) -> str:
    """복합 문제용 서론 로드맵(Type IV) 합성 — 각 토픽을 등장배경 원형으로, 본론을 로드맵으로."""
    circles = "".join(f'<div class="d-circle">{_esc(short_name(t)[:10])}</div>' for t in topics[:3])
    return (f'<div class="diagram d7"><div class="d-col">{circles}</div>'
            f'<span class="d-sep"></span><span class="d-arrow">→</span>'
            f'<div class="d-col"><div class="d-box d-hub">{_esc(joined[:24])}</div>'
            f'<div class="d-box soft">구성 (Ⅱ)</div>'
            f'<div class="d-box soft">비교 (Ⅲ)</div></div>'
            f'<span class="d-arrow">→</span><span class="d-sep"></span>'
            f'<div class="d-box">상호 관계 이해<small>활용 · 전망 (Ⅳ)</small></div></div>')


def assemble(question: str, kind: str, points: int, topics: list[dict],
             missing_names: list[str] | None = None,
             extra_sections: dict[str, str] | None = None,
             core_sections: dict[str, str] | None = None) -> dict:
    """적중 토픽들로 답안 본문을 조립한다.

    - topics: 적중 토픽 JSON (1~3건)
    - missing_names: 복합 문제 중 미적중 주제명
    - extra_sections: 미적중 주제명 → LLM이 집필한 소단락 프래그먼트 (부분 적중 1콜 결과)
    - core_sections: 뼈대 토픽 id → LLM이 보강한 Ⅱ단락 프래그먼트 (단일 토픽 경로에서 사용)
    반환: {body, title, mnemonic_html, slots, warnings}
    """
    missing_names = missing_names or []
    extra_sections = extra_sections or {}
    core_sections = core_sections or {}
    warnings: list[str] = []
    is_terms = "1교시" in str(kind)
    parts: list[str] = ['<p class="ans">답)</p>']
    slots = 1

    if len(topics) == 1:
        t = topics[0]
        s = short_name(t)
        kws = t.get("keywords") or []
        title = s
        if is_terms:
            # ---- 1교시형 3단락 (스펙 §4)
            # Ⅰ 제목은 "정의"형 — "개요·개념"은 방법론이 명시한 나쁜 사례 (체크리스트 M5)
            parts.append(f"<h2>{_esc(s)}의 정의</h2>")
            parts.append(f'<p class="def">{_emph(t.get("definition"), kws, quote=True)}</p>')
            slots += 2
            feats = t.get("features") or []
            if feats:
                d0 = feats[0].get("desc") or ""
                if isinstance(d0, (list, tuple)):  # 개조식 list desc는 첫 항목으로 축약
                    d0 = str(d0[0]) if d0 else ""
                parts.append(f"<h3>{_esc(s)}의 특징</h3>")
                parts.append('<p class="def">'
                             + _esc(" · ".join(f.get("item", "") for f in feats[:4])
                                    + " 특성 보유 — " + d0) + "</p>")
                slots += 1
            if _slot_structure(t, parts, warnings, r2=False,
                               core=core_sections.get(t.get("id"))):
                slots += 1
            parts.append("<h2>활용방안 및 결론</h2>")
            if t.get("usage"):
                parts.append(_t2(t["usage"], ("활용", "설명"), kws=kws))
            if t.get("conclusion"):
                parts.append(f'<p class="def">{_emph(t["conclusion"], kws, limit=1)}</p>')
            slots += 1
        else:
            # ---- 2교시형 4단락 (스펙 §5)
            _slot_intro_p2(t, parts, warnings)
            slots += 1
            if _slot_structure(t, parts, warnings, r2=True,
                               core=core_sections.get(t.get("id"))):
                slots += 1
            # Ⅲ. 특징·비교 (+ 미등록 소단락) — 물어본 지문 어구가 있으면 제목에
            # 그대로 스탬프 (체크리스트 M6·M12, 단순형 범위. 파서 전체는 M7~M10 배치)
            third: list[str] = []
            feats = t.get("features") or []
            if feats:
                third.append(f"<h3>{_esc(s)}의 주요 특징</h3>")
                third.append(_t2(feats, ("구분", "특징"), kws=kws))
            cmp0 = (t.get("comparisons") or [None])[0]
            if cmp0:
                third.append(f'<h3>{_esc(s)}와 {_esc(cmp0.get("vs_name"))}의 비교</h3>')
                third.append(_tcmp(cmp0.get("axes") or [], s, cmp0.get("vs_name") or ""))
            for name in missing_names:
                third.extend([extra_sections[name]] if name in extra_sections
                             else _missing_placeholder(name))
            if third:
                phrase = asked_phrase(question, [s] + list(t.get("aliases") or []))
                third_title = (f"{s}의 {phrase}" if phrase and phrase not in ("특징", "비교")
                               else f"{s}의 특징 및 비교")
                parts.append(f"<h2>{_esc(third_title)}</h2>")
                parts.extend(third)
                slots += 1
            else:
                warnings.append("특징/비교 부품 없음 — Ⅲ단락 생략")
            # Ⅳ. 결론
            parts.append("<h2>활용방안 및 기대효과</h2>")
            if t.get("usage"):
                parts.append(_t2(t["usage"], ("기대효과", "설명"), kws=kws))
            if t.get("conclusion"):
                parts.append(f'<p class="def">{_emph(t["conclusion"], kws, limit=1)}</p>')
            slots += 1
    else:
        # ---- 복합(2~3 토픽) — 2교시형 구조로 조립
        names = [short_name(t) for t in topics]
        title = " · ".join(names)
        joined = "와 ".join(names[:2]) if len(names) == 2 else " · ".join(names)
        parts.append(f"<h2>{_esc(joined)}의 개요</h2>")
        if not is_terms:
            parts.append(_composite_intro(topics, joined))  # Type IV 서론 로드맵 합성
        for t in topics:
            parts.append(f"<h3>{_esc(short_name(t))}의 정의</h3>")
            parts.append(f'<p class="def">{_esc(t.get("definition") or t.get("definition_long"))}</p>')
        slots += 1
        # Ⅱ. 구성 — 개념도는 1개(일도일표), 구성요소는 토픽별 축약
        parts.append(f"<h2>{_esc(joined)}의 구성</h2>")
        slots += 1
        diag_t = next((t for t in topics if t.get("diagram_html")), None)
        if diag_t:
            parts.append(f"<h3>{_esc(short_name(diag_t))}의 구성도</h3>")
            parts.append(diag_t["diagram_html"])
            # 본문 개념도 직후 간글 1줄 필수 (체크리스트 G1) — 부재 시 keywords 합성
            parts.append(f'<p class="gloss">'
                         f'{_esc(diag_t.get("diagram_gloss") or _auto_gloss(diag_t))}</p>')
        else:
            warnings.append("개념도 부품 없음")
        for t in topics:
            if t.get("components"):
                parts.append(f"<h3>{_esc(short_name(t))}의 구성요소</h3>")
                parts.append(_t3(t["components"][:3], r2=False))
        # Ⅲ. 비교 — 상호 comparisons 우선, 없으면 features 축 정렬 자동 대비표
        parts.append(f"<h2>{_esc(joined)}의 비교</h2>")
        slots += 1
        mutual = _mutual_comparison(topics)
        if mutual:
            base_t, c = mutual
            other = next((x for x in topics if x.get("id") == c.get("vs")), None)
            parts.append(_tcmp(c.get("axes") or [], short_name(base_t),
                               short_name(other) if other else (c.get("vs_name") or "")))
        else:
            parts.append(_tcmp(_auto_compare_axes(topics[0], topics[1]),
                               names[0], names[1]))
            warnings.append("상호 비교 부품 없음 — features 자동 대비표 사용")
        for name in missing_names:
            parts.extend([extra_sections[name]] if name in extra_sections
                         else _missing_placeholder(name))
        # Ⅳ. 활용·전망
        parts.append("<h2>활용방안 및 전망</h2>")
        slots += 1
        usage: list[dict] = []
        for t in topics:
            usage.extend((t.get("usage") or [])[:2])
        if usage:
            parts.append(_t2(usage[:4], ("기대효과", "설명")))
        concl = next((t.get("conclusion") for t in topics if t.get("conclusion")), "")
        if concl:
            parts.append(f'<p class="def">{_esc(concl)}</p>')

    # 두문자 암기 박스 — 토픽별 병렬 (박스 높이상 최대 2줄)
    mn_lines = []
    for t in topics[:2]:
        mn = t.get("mnemonic") or {}
        word = str(mn.get("word") or "").strip()
        exp = " · ".join(str(e) for e in (mn.get("expansion") or [])[:5])
        if word or exp:
            mn_lines.append(f"<p><b>{_esc(word or short_name(t))}</b> — {_esc(exp)}</p>")
    mnemonic_html = "\n    ".join(mn_lines) or "<p><b>핵심 키워드</b> — 소제목 첫 글자를 이어 암기하세요.</p>"

    return {
        "body": "\n".join(parts),
        "title": title,
        "mnemonic_html": mnemonic_html,
        "slots": slots,
        "warnings": warnings,
    }


def split_subjects(question: str) -> list[str]:
    """복합 문제의 주제어 후보 분리 — 부분 적중 감지용 휴리스틱.

    요구 어미(에 대하/을·를/의) 앞 서두를 나열 구분자(와/과/및/,/·)로 쪼갠다.
    한글 연결어 "와/과"는 단어 내부("성과 관리")에서도 걸리므로, 분리 잔여물 중
    범용어(_STOP_TOKENS: 관리/성과/체계 등)는 주제어에서 제외한다 — 이런 조각이
    매칭 미스로 흘러가면 "라이브러리 미등록 토픽" 쓰레기 소단락이 생긴다.
    """
    head = re.split(r"의 |에 대하|을 |를 |이란|비교", question)[0]
    parts = re.split(r"\s*(?:와|과|및|,|·)\s+|\s*[,·]\s*", head)
    out = []
    for p in parts:
        p = p.strip()
        if len(p) >= 2 and _norm(p) not in _STOP_TOKENS:
            out.append(p)
    return out
