"""토픽 부품 → 답안 본문 조립기 (LLM 0콜, docs/library-spec.md 2-4).

템플릿 슬롯(docs/answer-template-spec.md §4·§5)에 토픽 JSON 부품을 배치한다.
- 부품이 없는 선택 슬롯은 생략하고 경고만 남긴다 (조립이 깨지지 않게).
- 출력은 답안 본문 HTML 프래그먼트 — render_answer()가 답안지로 감싼다.
"""

from __future__ import annotations

import html as html_mod
import re


def _esc(s) -> str:
    return html_mod.escape(str(s or ""))


def short_name(topic: dict) -> str:
    """표시용 짧은 이름 (괄호 제거)."""
    base = re.sub(r"\([^)]*\)", " ", topic.get("name") or "")
    base = re.sub(r"\s+", " ", base).strip()
    return base or (topic.get("name") or topic.get("id") or "")


# ---------------------------------------------------------------- 표 빌더

def _t3(rows: list[dict], r2: bool = True, headers=("구분", "구성요소", "설명")) -> str:
    limit = 4 if r2 else 6
    cls = ' class="r2"' if r2 else ""
    trs = "".join(
        f"<tr{cls}><td>{_esc(r.get('role'))}</td><td>{_esc(r.get('name'))}</td>"
        f"<td>{_esc(r.get('detail'))}</td></tr>"
        for r in rows[:limit]
    )
    th = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    return f'<table class="t3"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'


def _t2(rows: list[dict], headers=("구분", "설명")) -> str:
    trs = "".join(
        f"<tr><td>{_esc(r.get('item'))}</td><td>{_esc(r.get('desc'))}</td></tr>"
        for r in rows[:4]
    )
    th = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    return f'<table class="t2"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'


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
    parts.append(f"<h2>{_esc(s)}의 개요</h2>")
    if t.get("intro_diagram_html"):
        parts.append(t["intro_diagram_html"])
    if t.get("definition") or t.get("definition_long"):
        # 35자 정의요약(definition)이 방법론의 키워드 나열형 정의에 부합 — 우선 사용
        parts.append(f'<p class="def">(정의) {_esc(t.get("definition") or t.get("definition_long"))}</p>')
    if t.get("background"):
        parts.append(f'<p class="def">(필요성) {_esc(t["background"])}</p>')
    elif not t.get("intro_diagram_html"):
        warnings.append("필요성(background) 부품 없음 — 서론 축약")


def _slot_structure(t: dict, parts: list, warnings: list, r2: bool) -> bool:
    """Ⅱ. 구성도+구성요소. 슬롯을 하나라도 채우면 True."""
    s = short_name(t)
    has = bool(t.get("diagram_html") or t.get("components"))
    if not has:
        warnings.append(f"{s}: 구성도/구성요소 부품 없음 — Ⅱ단락 생략")
        return False
    parts.append(f"<h2>{_esc(s)}의 구성도 및 구성요소</h2>")
    if t.get("diagram_html"):
        parts.append(f"<h3>{_esc(s)}의 구성도</h3>")
        parts.append(t["diagram_html"])
        if t.get("diagram_gloss"):
            parts.append(f'<p class="gloss">{_esc(t["diagram_gloss"])}</p>')
    if t.get("components"):
        parts.append(f"<h3>{_esc(s)}의 구성요소</h3>")
        parts.append(_t3(t["components"], r2=r2))
        if t.get("components_gloss"):
            parts.append(f'<p class="gloss">{_esc(t["components_gloss"])}</p>')
    return True


def _missing_placeholder(name: str) -> list[str]:
    """부분 적중에서 미등록 토픽 소단락 플레이스홀더 (키 없을 때)."""
    return [
        f"<h3>{_esc(name)}의 개요</h3>",
        f'<p class="def">(라이브러리 미등록 토픽) {_esc(name)}의 상세 부품이 라이브러리에 없어 '
        "요약을 생략함 — LLM 키 설정 시 자동 집필됨</p>",
    ]


def assemble(question: str, kind: str, points: int, topics: list[dict],
             missing_names: list[str] | None = None,
             extra_sections: dict[str, str] | None = None) -> dict:
    """적중 토픽들로 답안 본문을 조립한다.

    - topics: 적중 토픽 JSON (1~3건)
    - missing_names: 복합 문제 중 미적중 주제명
    - extra_sections: 미적중 주제명 → LLM이 집필한 소단락 프래그먼트 (부분 적중 1콜 결과)
    반환: {body, title, mnemonic_html, slots, warnings}
    """
    missing_names = missing_names or []
    extra_sections = extra_sections or {}
    warnings: list[str] = []
    is_terms = "1교시" in str(kind)
    parts: list[str] = ['<p class="ans">답)</p>']
    slots = 1

    if len(topics) == 1:
        t = topics[0]
        s = short_name(t)
        title = s
        if is_terms:
            # ---- 1교시형 3단락 (스펙 §4)
            parts.append(f"<h2>{_esc(s)}의 개요</h2>")
            parts.append(f"<h3>{_esc(s)}의 정의</h3>")
            parts.append(f'<p class="def">{_esc(t.get("definition"))}</p>')
            slots += 2
            feats = t.get("features") or []
            if feats:
                parts.append(f"<h3>{_esc(s)}의 특징</h3>")
                parts.append('<p class="def">'
                             + _esc(" · ".join(f.get("item", "") for f in feats[:4]) + " 특성 보유 — "
                                    + (feats[0].get("desc") or "")) + "</p>")
                slots += 1
            if _slot_structure(t, parts, warnings, r2=False):
                slots += 1
            parts.append("<h2>활용방안 및 결론</h2>")
            if t.get("usage"):
                parts.append(_t2(t["usage"], ("활용", "설명")))
            if t.get("conclusion"):
                parts.append(f'<p class="def">{_esc(t["conclusion"])}</p>')
            slots += 1
        else:
            # ---- 2교시형 4단락 (스펙 §5)
            _slot_intro_p2(t, parts, warnings)
            slots += 1
            if _slot_structure(t, parts, warnings, r2=True):
                slots += 1
            # Ⅲ. 특징·비교 (+ 미등록 소단락)
            third: list[str] = []
            feats = t.get("features") or []
            if feats:
                third.append(f"<h3>{_esc(s)}의 주요 특징</h3>")
                third.append(_t2(feats, ("구분", "특징")))
            cmp0 = (t.get("comparisons") or [None])[0]
            if cmp0:
                third.append(f'<h3>{_esc(s)}와 {_esc(cmp0.get("vs_name"))}의 비교</h3>')
                third.append(_tcmp(cmp0.get("axes") or [], s, cmp0.get("vs_name") or ""))
            for name in missing_names:
                third.extend([extra_sections[name]] if name in extra_sections
                             else _missing_placeholder(name))
            if third:
                parts.append(f"<h2>{_esc(s)}의 특징 및 비교</h2>")
                parts.extend(third)
                slots += 1
            else:
                warnings.append("특징/비교 부품 없음 — Ⅲ단락 생략")
            # Ⅳ. 결론
            parts.append("<h2>활용방안 및 기대효과</h2>")
            if t.get("usage"):
                parts.append(_t2(t["usage"], ("기대효과", "설명")))
            if t.get("conclusion"):
                parts.append(f'<p class="def">{_esc(t["conclusion"])}</p>')
            slots += 1
    else:
        # ---- 복합(2~3 토픽) — 2교시형 구조로 조립
        names = [short_name(t) for t in topics]
        title = " · ".join(names)
        joined = "와 ".join(names[:2]) if len(names) == 2 else " · ".join(names)
        parts.append(f"<h2>{_esc(joined)}의 개요</h2>")
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
            if diag_t.get("diagram_gloss"):
                parts.append(f'<p class="gloss">{_esc(diag_t["diagram_gloss"])}</p>')
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
    """
    head = re.split(r"의 |에 대하|을 |를 |이란|비교", question)[0]
    parts = re.split(r"\s*(?:와|과|및|,|·)\s+|\s*[,·]\s*", head)
    return [p.strip() for p in parts if p.strip() and len(p.strip()) >= 2]
