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

def _wlen(s: str) -> float:
    """손글씨 환산 글자 수 — 공백 제외, 영문·숫자 반각 0.5자 (발주자 규격 2026-07-17)."""
    return sum(0.5 if ord(ch) < 128 else 1.0 for ch in str(s) if not ch.isspace())


def _detail_cell(detail, kws=None, compact: bool = False) -> tuple[str, int]:
    """detail(문자열 또는 개조식 항목 list) → (셀 HTML, 행 줄수 1|2).

    체크리스트 T4~T6 + 손글씨 밀도: list는 "- 항목<br>- 항목" 개조식(2항목이면 2줄 행),
    문자열은 손글씨 한 줄(환산 11자) 초과면 2줄 행. compact=True(축약 표)는 1줄로 절삭.
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
    return _emph(s, kws, limit=1), (2 if _wlen(s) > 11 else 1)


def _t3(rows: list[dict], r2: bool = True, headers=("구분", "구성요소", "설명"),
        kws=None, max_rows: int | None = None) -> str:
    """구성요소 3단표 — 행 높이 자동 선택 + 1열 rowspan 병합 + 셀 개조식 (T3~T6).

    - 행 단위 r1/r2 자동: detail이 개조식 2항목 또는 29자 이상일 때만 2줄 행
      (환산 기준: 설명열 폭 60% ≈ 30자/줄 — "내용 1구절 r2" 반려 방지)
    - 연속 행의 role이 같으면 1열(구분)을 rowspan 병합 (실물 카테고리 병합 22~28%)
    - r2=False는 축약 모드(복합/1교시): 전행 1줄, detail은 1줄 분량으로 절삭
    - max_rows: 행 수 상한 재정의 (절차표 5단계 등 — 기본값은 현행 4/6 유지)
    - 기본 상한 적응(모범답안 감사 2-1): r2 모드라도 전 행이 1줄분(개조식 2항목
      없음·환산 11자 이하)이면 7행까지 허용 — ISO 22301 7조항, ISP 수행내용 7행 등
      1줄 행 다행 표가 4행에서 잘리지 않게. 2줄 행이 하나라도 있으면 현행 4행 유지.
    """
    if max_rows is None:
        one_line = all(
            not (isinstance(r.get("detail"), (list, tuple)) and len(r["detail"]) >= 2)
            and (isinstance(r.get("detail"), (list, tuple))
                 or _wlen(str(r.get("detail") or "")) <= 11)
            for r in rows)
        max_rows = (7 if one_line else 4) if r2 else 6
    rows = rows[:max_rows]
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


def _tcmp(axes: list[dict], a_name: str, b_name: str, auto_r2: bool = False,
          max_rows: int = 4) -> str:
    """비교표. auto_r2=True(요구 조립 경로 전용)면 셀이 손글씨 1줄분(환산 11자)을
    넘는 행을 2줄 행(r2)으로 — 임계값은 밀도 린터의 r2 미달 기준(≤11)과 정합.
    기존 호출(기본값)은 렌더 불변."""
    trs = []
    for r in axes[:max_rows]:
        a, b = str(r.get("a") or ""), str(r.get("b") or "")
        cls = ' class="r2"' if auto_r2 and max(_wlen(a), _wlen(b)) > 11 else ""
        trs.append(f"<tr{cls}><td>{_esc(r.get('axis'))}</td>"
                   f"<td>{_esc(a)}</td><td>{_esc(b)}</td></tr>")
    return (f'<table class="tcmp"><thead><tr><th>구분</th><th>{_esc(a_name)}</th>'
            f"<th>{_esc(b_name)}</th></tr></thead><tbody>{''.join(trs)}</tbody></table>")


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


def _pick_comparison(t: dict, question: str) -> dict | None:
    """comparisons 중 문항 어구가 지목한 축 선택 — 없으면 첫 항목 (감사 C3).

    모범답안 축(예: ITSM↔전통 IT운영)을 추가해도 기존 문항 렌더는 불변이고,
    문항이 해당 상대를 언급할 때만 그 축이 뽑힌다.
    """
    comps = t.get("comparisons") or []
    if not comps:
        return None
    qn = _norm(question or "")
    for c in comps:
        for nm in (c.get("vs_name"), c.get("a_name")):
            n = _norm(nm or "")
            if len(n) >= 2 and n in qn:
                return c
    return comps[0]


def _section_table(sec: dict, kws=None) -> str:
    """확장 단락 부품(sections, 스키마 v1.2) → 표 HTML (t3/t2)."""
    rows = sec.get("rows") or []
    if sec.get("kind") == "t2":
        return _t2(rows, ("구분", "설명"), kws=kws)
    return _t3(rows, r2=True, headers=("구분", "항목", "설명"), kws=kws)


def _intro_title(t: dict) -> str:
    """Ⅰ 리드문형 제목 (감사 C1 — 모범답안 전건 '리드문, ○○의 개요' 형)."""
    s = short_name(t)
    lead = str(t.get("lead") or "").strip()
    return f"{lead}, {s}의 개요" if lead else f"{s}의 개요"


# ---------------------------------------------------------------- 슬롯 조립

def _slot_intro_p2(t: dict, parts: list, warnings: list) -> None:
    """2교시 Ⅰ. 서론 — intro_diagram 있으면 Type IV 로드맵, 없으면 Type I(정의+필요성)."""
    s = short_name(t)
    kws = t.get("keywords") or []
    parts.append(f"<h2>{_esc(_intro_title(t))}</h2>")
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
            # Ⅲ. 확장 단락 — 토픽별 확장 부품(sections)이 있으면 우선 배치 (감사 C2:
            # 모범답안의 절차/검토항목/위험·해결 단락), 없으면 특징 및 비교 (현행).
            # 물어본 지문 어구가 있으면 제목에 그대로 스탬프 (체크리스트 M6·M12)
            third: list[str] = []
            sec0 = (t.get("sections") or [None])[0]
            if sec0:
                third.append(_section_table(sec0, kws=kws))
            else:
                feats = t.get("features") or []
                if feats:
                    third.append(f"<h3>{_esc(s)}의 주요 특징</h3>")
                    third.append(_t2(feats, ("구분", "특징"), kws=kws))
            cmp0 = _pick_comparison(t, question)
            if cmp0:
                a_name = cmp0.get("a_name") or s
                third.append(f'<h3>{_esc(a_name)}와 {_esc(cmp0.get("vs_name"))}의 비교</h3>')
                third.append(_tcmp(cmp0.get("axes") or [], a_name, cmp0.get("vs_name") or ""))
            for name in missing_names:
                third.extend([extra_sections[name]] if name in extra_sections
                             else _missing_placeholder(name))
            if third:
                phrase = asked_phrase(question, [s] + list(t.get("aliases") or []))
                if phrase and phrase not in ("특징", "비교"):
                    third_title = f"{s}의 {phrase}"
                elif sec0:
                    third_title = str(sec0.get("title") or f"{s}의 특징 및 비교")
                else:
                    third_title = f"{s}의 특징 및 비교"
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

    # 두문자 암기 박스 — 토픽별 병렬 (박스 높이상 최대 2줄, _mnemonic_lines 공용)
    mnemonic_html = _mnemonic_lines(topics)

    return {
        "body": "\n".join(parts),
        "title": title,
        "mnemonic_html": mnemonic_html,
        "slots": slots,
        "warnings": warnings,
    }


# ================================================================ 요구 주도 조립
# (docs/question-spec.md 2-2·2-3·2-5 — 2교시형 · 요구 2개 이상일 때만 사용.
#  단순형(N=0~1)·1교시형은 위 assemble() 표준 구조를 그대로 쓴다.)

from _question import clip_label, req_title  # noqa: E402  (순환 없음)

_ROMAN = "ⅠⅡⅢⅣⅤⅥⅦ"

# 슬롯 → 부품 대체 순서 (question-spec 2-2 표: 1순위 부재 시 경고 없이 강등)
_SLOT_CHAIN = {
    "definition_long": ("definition_long", "definition"),
    "background": ("background", "definition_long", "definition"),
    "components": ("components", "features"),
    "diagram": ("diagram_html",),
    "procedure": ("procedure", "components"),
    "comparisons": ("comparisons",),
    "usage": ("usage", "exam_points"),
    "features": ("features", "components"),
}

# 분량 재배분 (2-5): 요구 수 → 요구 단락당 (최소, 최대) 줄 — 최소 7줄은 별도 보장
_REQ_BOUNDS = {1: (18, 22), 2: (18, 22), 3: (13, 17), 4: (9, 12), 5: (9, 12)}


def _block_ln(block: str) -> int:
    """블록 1개의 점유 줄 수 — _agents._block_lines와 동일 규칙 (조립 시 예산 계측용)."""
    b = block.lstrip().lower()
    if b.startswith("<h2") or b.startswith("<h3"):
        return 1
    if b.startswith("<p"):
        return 2 if 'class="def"' in b[:40] else 1
    if b.startswith("<table"):
        return (len(re.findall(r"<tr[\s>]", b))
                + len(re.findall(r'<tr\s+class="r2"', b)))
    if b.startswith("<div"):
        head = b[:60]
        if 'class="diagram d7"' in head:
            return 7
        if 'class="diagram"' in head:
            return 6
    return 1


def _proc_table(proc: list[dict], kws=None, max_rows: int = 5) -> str:
    """절차표 (t3 변형) — 구분열 'N단계' + 절차명 + 수행 내용 (스키마 v1.1)."""
    rows = [{"role": f"{p.get('step')}단계", "name": p.get("name"),
             "detail": p.get("desc")} for p in proc[:max_rows]]
    return _t3(rows, r2=True, headers=("단계", "절차", "수행 내용"), kws=kws,
               max_rows=max_rows)


def _sub_point_rows(rows: list[dict], sub_points: list[str], key: str = "role"):
    """괄호 지정(sub_points)과 role이 맞는 행만 지정 순서로 — 표 구분열 반영 (2-2)."""
    if not sub_points:
        return None
    out = []
    for sp in sub_points:
        spn = _norm(sp)
        for r in rows:
            rn = _norm(str(r.get(key) or ""))
            if rn and (spn in rn or rn in spn) and r not in out:
                out.append(r)
    return out if len(out) >= max(2, len(sub_points) - 1) else None


class _Ctx:
    """조립 중 공유 상태 — 부품 중복 사용·개념도 총량(일도일표) 추적."""

    def __init__(self):
        self.used: set[tuple] = set()
        self.diagrams = 0

    def take(self, t: dict, field: str):
        """부품이 있고 아직 안 썼으면 반환+사용 처리, 아니면 None."""
        v = t.get(field)
        key = (t.get("id"), field)
        if not v or key in self.used:
            return None
        self.used.add(key)
        return v


def _req_h2_title(r: dict, t: dict) -> str:
    """단락 제목 — 지문 어구 그대로 스탬프. 너무 짧으면 토픽명으로 보완."""
    title = req_title(r)
    s = short_name(t)
    if len(title) <= 6 and s and _norm(s) not in _norm(title):
        title = f"{s}의 {title}"
    return title[:30]


def _req_section(r: dict, ts: list[dict], primary: dict, bounds: tuple[int, int],
                 ctx: _Ctx, warnings: list, first_of: dict) -> dict:
    """요구 1건 → 단락 블록들. 반환 {title, blocks, lines, deficit}."""
    bmin, bmax = bounds
    t = ts[0] if ts else primary
    kws = t.get("keywords") or []
    title = _req_h2_title(r, t)
    blocks: list[str] = [f"<h2>{_esc(title)}</h2>"]
    lines = 1
    s = short_name(t)

    def add(html: str) -> bool:
        nonlocal lines
        ln = _block_ln(html)
        if html and lines + ln <= bmax:
            blocks.append(html)
            lines += ln
            return True
        return False

    def add_def(text, prefix="", quote=False):
        if not text:
            return False
        return add(f'<p class="def">{prefix}{_emph(text, kws, quote=quote)}</p>')

    def add_t2(rows, headers, h3=None):
        if not rows:
            return False
        html = _t2(rows[:4], headers, kws=kws)
        pre = f"<h3>{_esc(h3)}</h3>" if h3 else ""
        ln = _block_ln(html) + (1 if pre else 0)
        if lines + ln > bmax:
            html = _t2(rows[:2], headers, kws=kws)  # 압축
            if lines + _block_ln(html) + (1 if pre else 0) > bmax:
                return False
        if pre:
            add(pre)
        return add(html)

    def add_t3(rows, h3, headers=("구분", "구성요소", "설명")):
        if not rows:
            return False
        # 다행(1줄 행) 표 → 9줄 상세표 → 7줄 압축표 순 시도 (2-5 + 감사 2-1 7행 표)
        for n in sorted({min(7, len(rows)), 5, 4, 3, 2}, reverse=True):
            if n > len(rows):
                continue
            html = _t3(rows[:n], r2=True, headers=headers, kws=kws, max_rows=n)
            if lines + 1 + _block_ln(html) <= bmax:
                add(f"<h3>{_esc(h3)}</h3>")
                return add(html)
        return False

    def add_diagram(tp):
        nonlocal lines
        if ctx.diagrams >= 2 or lines + 8 > bmax:
            return False
        d = ctx.take(tp, "diagram_html")
        if not d:
            return False
        ctx.diagrams += 1
        add(f"<h3>{_esc(short_name(tp))}의 구성도</h3>")
        blocks.append(d)  # 부품 HTML은 add() 계측을 우회하므로 직접 가산
        lines += _block_ln(d)
        add(f'<p class="gloss">{_esc(tp.get("diagram_gloss") or _auto_gloss(tp))}</p>')
        return True

    content0 = len(blocks)

    # ---- 비교 요구 (verb=compare): 상호 comparisons → 자동 대비표 순
    if r.get("verb") == "compare" or "comparisons" in (r.get("slots") or []):
        a = ts[0] if ts else primary
        b = ts[1] if len(ts) > 1 else None
        if b is None and a.get("id") != primary.get("id"):
            a, b = primary, a  # 지문에 한쪽만 언급 → 주 토픽과 짝
        cmp_done = False
        if b is not None:
            # b가 이 답안 첫 등장이면 정의 1건 먼저 (지문 어구 아래 근거) —
            # 접두 포함 2줄 문단 상한(40) 안에 드는 정의를 선택 (밀도 규격)
            if first_of.get(b.get("id")) == r.get("label"):
                tok = (short_name(b).split() or [short_name(b)])[0][:8]
                for field in ("definition_long", "definition"):
                    cand = b.get(field)
                    if cand and _wlen(f"({tok}) {cand}") <= 40:
                        ctx.take(b, field)
                        add_def(cand, f"({_esc(tok)}) ")
                        break
            mutual = _mutual_comparison([a, b])
            if mutual:
                base_t, c = mutual
                other = b if base_t.get("id") == a.get("id") else a
                add(f"<h3>{_esc(short_name(base_t))}와 {_esc(short_name(other))}의 비교</h3>")
                cmp_done = add(_tcmp(c.get("axes") or [], short_name(base_t),
                                     short_name(other), auto_r2=True))
            else:
                # 상호 comparisons 부재 → features 자동 대비표 (2-4: 대체 강등은 경고 없이)
                add(f"<h3>{_esc(short_name(a))}와 {_esc(short_name(b))}의 비교</h3>")
                cmp_done = add(_tcmp(_auto_compare_axes(a, b), short_name(a),
                                     short_name(b), auto_r2=True))
        else:
            cmp0 = _pick_comparison(t, r.get("text") or "")  # 문항 어구 지목 축 우선 (C3)
            if cmp0:
                a_name = cmp0.get("a_name") or s
                add(f'<h3>{_esc(a_name)}와 {_esc(cmp0.get("vs_name"))}의 비교</h3>')
                cmp_done = add(_tcmp(cmp0.get("axes") or [], a_name,
                                     cmp0.get("vs_name") or "", auto_r2=True))
        if not cmp_done:
            pass  # 아래 부족 판정으로
    else:
        # ---- 확장 단락 부품(sections) 우선: 요구 어구가 keys를 지목하면 그 표 배치 (C2)
        for i_sec, sec in enumerate(t.get("sections") or []):
            keyn = [_norm(k) for k in (sec.get("keys") or [])]
            rtxt = _norm(r.get("text") or "")
            if any(k and k in rtxt for k in keyn):
                key = (t.get("id"), f"section{i_sec}")
                if key not in ctx.used:
                    html = _section_table(sec, kws=kws)
                    if lines + 1 + _block_ln(html) <= bmax:
                        ctx.used.add(key)
                        add(f"<h3>{_esc(sec.get('title') or s)}</h3>")
                        add(html)
                break
        # ---- 일반 요구: 슬롯 순서대로 부품 배치 (대체 순서 강등)
        for slot in r.get("slots") or ["components"]:
            for field in _SLOT_CHAIN.get(slot, (slot,)):
                done = False
                if field == "diagram_html":
                    done = add_diagram(t)
                elif field == "procedure":
                    proc = ctx.take(t, "procedure")
                    if proc:
                        html = _proc_table(proc, kws=kws,
                                           max_rows=5 if bmax - lines >= 12 else 4)
                        if lines + 1 + _block_ln(html) <= bmax:
                            add(f"<h3>{_esc(s)}의 절차</h3>")
                            done = add(html)
                        if not done:
                            ctx.used.discard((t.get("id"), "procedure"))
                elif field == "components":
                    rows = ctx.take(t, "components")
                    if rows:
                        picked = _sub_point_rows(rows, r.get("sub_points") or []) or rows
                        done = add_t3(picked, f"{s}의 구성요소")
                elif field == "features":
                    rows = ctx.take(t, "features")
                    if rows:
                        done = add_t2(rows, ("구분", "특징"), h3=f"{s}의 주요 특징")
                elif field == "usage":
                    rows = ctx.take(t, "usage")
                    if rows:
                        head = "활용방안" if r.get("verb") in ("propose", "apply") else "기대효과"
                        done = add_t2(rows, (head, "설명"), h3=f"{s}의 {head}")
                elif field == "exam_points":
                    pts = ctx.take(t, "exam_points")
                    if pts:
                        rows = [{"item": f"포인트{i + 1}", "desc": p}
                                for i, p in enumerate(pts[:3])]
                        done = add_t2(rows, ("구분", "적용 포인트"))
                elif field == "background":
                    done = add_def(ctx.take(t, "background"), "(필요성) ")
                elif field in ("definition_long", "definition"):
                    done = add_def(ctx.take(t, field), "(정의) ", quote=True)
                if done:
                    break

    deficit = len(blocks) == content0
    if deficit:
        # 부품 전멸 — 플레이스홀더 표 (키 있으면 조립 후 LLM 일괄 집필로 대체, 2-4)
        blocks.append(
            f'<table class="t2"><thead><tr><th>구분</th><th>내용</th></tr></thead><tbody>'
            f'<tr><td>요구</td><td>{_esc(req_title(r)[:24])}</td></tr>'
            f'<tr><td>안내</td><td>라이브러리 미등록 부품</td></tr></tbody></table>')
        lines += 3
        warnings.append(f"요구 '{req_title(r)[:16]}' 부품 없음 — 플레이스홀더")

    # ---- 분량 미달 시 미사용 부품으로 보강 (내용 근접도 순 고정 풀 —
    #      구성도는 정의·설명형 요구에만: 방안·적용 단락에 구성도는 동문서답)
    if not deficit:
        pads = [
            lambda: add_def(ctx.take(t, "definition_long")
                            or (None if (t.get("id"), "definition_long") in ctx.used
                                else ctx.take(t, "definition")), "(정의) ", quote=True),
            lambda: add_t3(ctx.take(t, "components") or [], f"{s}의 구성요소"),
            lambda: (add_diagram(t) if r.get("verb") in ("define", "explain") else False),
            lambda: add_t2(ctx.take(t, "features") or [], ("구분", "특징"),
                           h3=f"{s}의 주요 특징"),
            lambda: add_def(ctx.take(t, "background"), "(필요성) "),
            lambda: add_t2(ctx.take(t, "usage") or [], ("기대효과", "설명"),
                           h3=f"{s}의 기대효과"),
            lambda: add_def(ctx.take(t, "components_gloss")),
            lambda: add_t2([{"item": f"포인트{i + 1}", "desc": p}
                            for i, p in enumerate((ctx.take(t, "exam_points") or [])[:3])],
                           ("구분", "출제 포인트")),
        ]
        for pad in pads:
            if lines >= bmin:
                break
            pad()

    return {"title": title, "blocks": blocks, "lines": lines,
            "deficit": deficit, "label": r.get("label")}


def _dyn_intro(primary: dict, hub: str, labels: list[str], warnings: list,
               ctx: _Ctx) -> list[str]:
    """서론 동적 로드맵 (Type IV, 2-3) — d-box 라벨 = 요구 단락 제목 요약(12자 절삭).

    서론 정의는 짧은 definition을 소비(ctx 마킹)하고 definition_long은 본론 정의
    요구에 보존한다 — 서론·본론 중복 렌더 방지. 뼈대 토픽(definition뿐)은 마킹
    없이 사용해 본론이 플레이스홀더로 강등되지 않게 한다.
    """
    s = short_name(primary)
    cat = str(primary.get("category") or "")
    if "보안" in cat or "sec" in cat.lower():
        h2 = f"{s}의 중요성 및 개요"  # Type III 리드문 제목 차용
    else:
        h2 = _intro_title(primary)  # 리드문형 제목 (감사 C1 — lead 필드)
    boxes = "".join(
        f'<div class="d-box soft">{_esc(lab)}<small>{_ROMAN[i + 1]} 단락</small></div>'
        for i, lab in enumerate(labels))
    goal = ((primary.get("usage") or [{}])[0].get("item") or "활용·기대효과")
    d7 = (f'<div class="diagram d7">'
          f'<div class="d-box d-hub">{_esc(hub[:12])}</div>'
          f'<span class="d-arrow">→</span><span class="d-sep"></span>'
          f'<div class="d-col">{boxes}</div>'
          f'<span class="d-sep"></span><span class="d-arrow">→</span>'
          f'<div class="d-box">{_esc(str(goal)[:12])}<small>물어본 순서 목차</small></div>'
          f"</div>")
    parts = [f"<h2>{_esc(h2)}</h2>", d7]
    kws = primary.get("keywords") or []
    short_def, long_def = primary.get("definition"), primary.get("definition_long")
    if long_def and short_def:
        ctx.take(primary, "definition")
        d = short_def  # 긴 정의는 본론(정의 요구)에 보존
    elif long_def:
        d = ctx.take(primary, "definition_long")
    else:
        d = short_def  # 뼈대 토픽 — 마킹 없이 사용
    if d:
        parts.append(f'<p class="def">(정의) {_emph(d, kws, quote=True)}</p>')
    else:
        warnings.append("서론 정의 부품 없음")
    bg = ctx.take(primary, "background")
    if bg:
        parts.append(f'<p class="def">(필요성) {_emph(bg, kws, limit=1)}</p>')
    return parts


def assemble_requirements(question: str, kind: str, points: int, parsed: dict,
                          assignments: list[list[dict]],
                          extra_sections: dict[str, str] | None = None) -> dict:
    """요구사항 리스트 → 단락 가변 조립 (2교시형 · N>=2 전용, LLM 0콜).

    - parsed: _question.parse_question 결과 / assignments: 요구별 토픽 dict 목록
    - extra_sections: 요구 label → LLM이 집필한 단락 내부 프래그먼트 (부족 슬롯 1콜 결과,
      h2는 서버가 지문 어구로 스탬프하므로 h2 없는 내부 블록만)
    반환: assemble()과 동일 + deficits(부족 요구 목록)·roadmap(박스 라벨 목록)
    """
    extra_sections = extra_sections or {}
    reqs = parsed.get("requirements") or []
    n = len(reqs)
    warnings: list[str] = []
    ctx = _Ctx()

    # 주 토픽 = 첫 배정 토픽. 배정 없는 요구는 주 토픽으로 조립
    primary = next((ts[0] for ts in assignments if ts), None)
    if primary is None:
        raise ValueError("assignments에 토픽 없음")
    topics_seen: list[dict] = []
    for ts in assignments:
        for t in ts or []:
            if all(t.get("id") != x.get("id") for x in topics_seen):
                topics_seen.append(t)

    # 요구별 토픽 첫 등장 라벨 (비교 단락의 상대 정의 배치용)
    first_of: dict[str, str] = {}
    for r, ts in zip(reqs, assignments):
        for t in ts or []:
            first_of.setdefault(t.get("id"), r.get("label"))

    # 서론 로드맵 허브: 리드 주제어(문두의 물음 대상)가 짧으면 그것, 아니면 주 토픽
    hub = short_name(primary)
    for pat in (r"^\s*([A-Za-z0-9·\s가-힣]{2,12}?)에\s*대(?:하여|해)",
                r"^\s*([A-Za-z0-9·\s가-힣]{2,12}?)(?:의|을|를|은|는)\s"):
        m = re.match(pat, question or "")
        if m and not re.search(r"다음|아래", m.group(1)) and len(m.group(1).strip()) >= 2:
            hub = m.group(1).strip()
            break

    bounds = _REQ_BOUNDS.get(n, _REQ_BOUNDS[5])
    bounds = (max(bounds[0], 7), bounds[1])  # 요구당 최소 7줄 보장

    # ---- 서론 (요구 단락보다 먼저 조립 — 짧은 정의·필요성을 서론이 먼저 소비해
    #      본론 중복 렌더를 막는다. 라벨은 단락 제목을 미리 계산: 박스 텍스트 ⊂ h2)
    titles = [_req_h2_title(r, ((ts or [primary])[0])) for r, ts in zip(reqs, assignments)]
    labels = [clip_label(t) for t in titles]
    intro_parts = _dyn_intro(primary, hub, labels, warnings, ctx)

    # ---- 요구 단락들
    sections: list[dict] = []
    for i, (r, ts) in enumerate(zip(reqs, assignments)):
        b = bounds
        if n >= 3 and i == n - 1:
            b = (bounds[0], max(7, bounds[1] - 2))  # 마지막 단락 말미 결론 2줄 자리
        sec = _req_section(r, ts or [], primary, b, ctx, warnings, first_of)
        if sec["deficit"] and sec["label"] in extra_sections:
            frag = extra_sections[sec["label"]]
            sec["blocks"] = [sec["blocks"][0], frag]  # h2(지문 스탬프) + LLM 프래그먼트
            sec["lines"] = 1 + sum(_block_ln(b2) for b2 in _split_blocks_frag(frag))
            sec["deficit"] = False
            warnings[:] = [w for w in warnings
                           if not w.startswith(f"요구 '{req_title(r)[:16]}'")]
        sections.append(sec)

    # ---- 결론 (2-2 표): N=2 → Ⅳ 결론 단락 / N>=3 → 마지막 단락 말미 2줄
    concl_t = next((t for t in topics_seen if t.get("conclusion")), None)
    if n <= 2:
        blocks = ["<h2>결론 및 기대효과</h2>"]
        rows = ctx.take(primary, "usage") or ctx.take(primary, "exam_points")
        if rows and isinstance(rows[0], str):
            rows = [{"item": f"포인트{i + 1}", "desc": p} for i, p in enumerate(rows[:3])]
        if rows:
            blocks.append(_t2(rows[:3], ("기대효과", "설명"), kws=primary.get("keywords")))
        if concl_t:
            blocks.append(f'<p class="def">{_emph(concl_t["conclusion"], concl_t.get("keywords"), limit=1)}</p>')
        if len(blocks) > 1:
            sections.append({"title": "결론 및 기대효과", "blocks": blocks,
                             "lines": sum(_block_ln(b2) for b2 in blocks),
                             "deficit": False, "label": None})
    elif concl_t:
        sections[-1]["blocks"].append(
            f'<p class="def">{_emph(concl_t["conclusion"], concl_t.get("keywords"), limit=1)}</p>')
        sections[-1]["lines"] += 2

    parts: list[str] = ['<p class="ans">답)</p>']
    parts.extend(intro_parts)
    for sec in sections:
        parts.extend(sec["blocks"])

    names = [short_name(t) for t in topics_seen[:3]]
    deficits = [
        {"label": sec["label"], "title": sec["title"],
         "text": reqs[i]["text"], "sub_points": reqs[i]["sub_points"],
         "keywords": ((assignments[i] or [primary])[0].get("keywords") or [])[:5]}
        for i, sec in enumerate(sections[:n]) if sec["deficit"]
    ]
    return {
        "body": "\n".join(parts),
        "title": " · ".join(names) if len(names) > 1 else (names[0] if names else hub),
        "mnemonic_html": _mnemonic_lines(topics_seen),
        "slots": 1 + len(sections),
        "warnings": warnings,
        "deficits": deficits,
        "roadmap": labels,
    }


def _split_blocks_frag(html: str) -> list[str]:
    """LLM 프래그먼트의 최상위 블록 분해 (줄 수 계측용 — 단순 태그 경계)."""
    return re.findall(r"<(?:h3|p|table|div)\b.*?</(?:h3|p|table|div)>|<(?:h3|p)\b[^>]*>[^<]*",
                      html or "", re.S) or ([html] if html else [])


def _mnemonic_lines(topics: list[dict]) -> str:
    """두문자 암기 박스 — 토픽별 병렬 (표준·요구 조립 공용, 박스 최대 2줄).

    단일 토픽이고 mnemonic.extra("보조어 — 풀이", 스키마 v1.2)가 있으면 둘째 줄로
    병기한다 (감사 2-2: ISP 사실규·필시중 등 보조 암기축).
    """
    mn_lines = []
    for t in topics[:2]:
        mn = t.get("mnemonic") or {}
        word = str(mn.get("word") or "").strip()
        exp = " · ".join(str(e) for e in (mn.get("expansion") or [])[:5])
        if word or exp:
            mn_lines.append(f"<p><b>{_esc(word or short_name(t))}</b> — {_esc(exp)}</p>")
    if len(topics) == 1 and len(mn_lines) == 1:
        extra = str((topics[0].get("mnemonic") or {}).get("extra") or "").strip()
        if extra:
            head, _, tail = extra.partition(" — ")
            mn_lines.append(f"<p><b>{_esc(head)}</b> — {_esc(tail)}</p>" if tail
                            else f"<p>{_esc(extra)}</p>")
    return ("\n    ".join(mn_lines)
            or "<p><b>핵심 키워드</b> — 소제목 첫 글자를 이어 암기하세요.</p>")


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
