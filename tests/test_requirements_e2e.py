"""요구 주도 조립 E2E — question-spec 3장 완료 기준 (키 없이, LLM 0콜).

검증 문항 Q1~Q5(부록 A 표본·전부 라이브러리 적중 토픽)를 파이프라인 전체로 돌려
form/요구 수/단락 순서/로드맵/분량/부품 존재를 검사한다.
실행: python3 tests/test_requirements_e2e.py
"""

import asyncio
import html as html_mod
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

QS = {
    "Q1": ("다음 내용을 설명하시오. 가. ISP의 정의 및 목적 나. ISP 수행방법론 체계와 절차 "
           "다. ISP, ISMP 비교 (25점)", "sub", 3, ["MG-047", "MG-048"]),
    "Q2": ("BCP에 대해 다음을 설명하시오. 가. ISO 22301의 주요 내용 나. BCP 달성을 위한 "
           "PDCA 사이클 적용 다. BIA 사례 적용 (25점)", "sub", 3, ["MG-015", "MG-069", "MG-019"]),
    "Q3": ("정보시스템 운영 성과측정 지표에 대하여 설명하시오 (비용측면, 업무측면) (25점)",
           "simple", 1, ["MG-166"]),
    "Q4": ("다음 마이닝 기법에 대하여 설명하시오. 1) 텍스트 마이닝 2) 웹 마이닝 "
           "3) 프로세스 마이닝 (25점)", "sub", 3, ["MG-011", "MG-009", "MG-008"]),
    "Q5": ("SWOT 분석의 구성요소를 설명하고 BCG Matrix와 비교한 후 활용방안을 제시하시오 (25점)",
           "inline", 3, ["MG-024", "MG-049"]),
}

_RESULTS: dict[str, dict] = {}


def _pipeline(msg):
    for k in ("GROQ_API_KEY", "GEMINI_API_KEY", "LLM_API_KEY"):
        os.environ.pop(k, None)
    import _agents

    async def run():
        return [e async for e in _agents.run_pipeline([], msg)]

    return asyncio.run(run())


def _result(name):
    if name not in _RESULTS:
        _RESULTS[name] = _pipeline(QS[name][0])[-1]
    return _RESULTS[name]


def _h2s(art):
    return [html_mod.unescape(re.sub(r"<[^>]+>", "", h)).strip()
            for h in re.findall(r"<h2[^>]*>.*?</h2>", art, re.S)]


def _sections(art):
    """artifact HTML을 h2 기준 (제목, 내용) 조각으로 (페이지 래퍼 무시)."""
    parts = re.split(r"(<h2[^>]*>.*?</h2>)", art, flags=re.S)
    out = []
    for i, p in enumerate(parts):
        if p.startswith("<h2"):
            title = html_mod.unescape(re.sub(r"<[^>]+>", "", p)).strip()
            out.append((title, parts[i + 1] if i + 1 < len(parts) else ""))
    return out


def test_all_hit_zero_llm():
    """Q1~Q5: library=true, llm_calls==0, form·요구 수 기대값, pages 3~4."""
    for name, (_, form, n, ids) in QS.items():
        r = _result(name)
        assert r.get("library") is True, (name, r.get("reply", "")[:80])
        assert r.get("llm_calls") == 0, (name, r.get("llm_calls"))
        q = r.get("question") or {}
        assert q.get("form") == form and q.get("requirements") == n, (name, q)
        assert r.get("matched") == ids, (name, r.get("matched"))
        assert r["sheet"]["pages"] in (3, 4), (name, r["sheet"])


def test_h2_order_follows_requirements():
    """h2 등장 순서 = 요구 원문 순서 (핵심 명사가 해당 순번 h2에 포함)."""
    expects = {
        "Q1": ["정의", "절차", "비교"],
        "Q2": ["ISO 22301", "PDCA", "BIA"],
        "Q4": ["텍스트 마이닝", "웹 마이닝", "프로세스 마이닝"],
        "Q5": ["구성요소", "비교", "활용방안"],
    }
    for name, keys in expects.items():
        h2s = _h2s(_result(name)["artifact"]["html"])
        req_h2s = h2s[1:1 + len(keys)]  # Ⅰ 서론 다음부터 요구 단락
        for i, k in enumerate(keys):
            assert k in req_h2s[i], (name, i, k, req_h2s)


def test_q1_procedure_and_tcmp():
    """Q1: '체계와 절차' 단락에 절차표, '비교' 단락에 tcmp."""
    secs = _sections(_result("Q1")["artifact"]["html"])
    proc_sec = next(c for t, c in secs if "절차" in t)
    assert "<th>단계</th>" in proc_sec and "환경분석" in proc_sec, proc_sec[:300]
    cmp_sec = next(c for t, c in secs if "비교" in t)
    assert 'class="tcmp"' in cmp_sec and "ISMP" in cmp_sec


def test_q2_topic_per_section_and_roadmap():
    """Q2: 단락 Ⅱ~Ⅳ가 각 토픽 부품으로 구성 + 서론 d7 박스 3개 텍스트 ⊂ 해당 h2."""
    art = _result("Q2")["artifact"]["html"]
    secs = _sections(art)[1:]  # Ⅰ 서론 제외 — 요구 단락만
    for key, topic_word in (("ISO 22301", "BCMS"), ("PDCA", "Plan"), ("BIA", "영향")):
        sec = next(c for t, c in secs if key in t)
        assert topic_word in sec, (key, topic_word, sec[:400])
    d7 = re.search(r'<div class="diagram d7">.*?</div>\s*<p class="def">', art, re.S).group()
    boxes = [html_mod.unescape(re.sub(r"<[^>]+>", "", b)).strip() for b in
             re.findall(r'<div class="d-box soft">(.*?)(?:<small|</div>)', d7, re.S)]
    h2s = _h2s(art)[1:4]
    assert len(boxes) == 3, boxes
    for b, h in zip(boxes, h2s):
        assert b in h, (b, h)


def test_q3_sub_points_in_table():
    """Q3: sub_points(비용측면·업무측면)가 표 구분열 또는 h3로 등장."""
    art = _result("Q3")["artifact"]["html"]
    for sp in ("비용측면", "업무측면"):
        assert re.search(rf"<td[^>]*>{sp}</td>|<h3[^>]*>[^<]*{sp}", art), sp


def test_q4_each_target_gets_section():
    """Q4: 대상 3건이 각각 단락을 얻고 마이닝 3토픽 matched."""
    r = _result("Q4")
    secs = _sections(r["artifact"]["html"])[1:]  # Ⅰ 서론 제외
    for key, word in (("텍스트 마이닝", "형태소"), ("웹 마이닝", "링크"), ("프로세스 마이닝", "로그")):
        sec = next((c for t, c in secs if key in t), None)
        assert sec and word in sec, (key, word)


def test_q5_compound_split_and_auto_tcmp():
    """Q5: 복동사 3분해(설명/비교/제시), 비교 단락에 SWOT×BCG 자동 대비표."""
    r = _result("Q5")
    secs = _sections(r["artifact"]["html"])
    cmp_sec = next(c for t, c in secs if "비교" in t)
    assert 'class="tcmp"' in cmp_sec
    assert "SWOT" in cmp_sec and "BCG" in cmp_sec
    usage_sec = next(c for t, c in secs if "활용방안" in t)
    assert "<table" in usage_sec


def test_n3_section_line_budget():
    """N=3 문항의 요구 단락이 각 12~17줄(0.7쪽±) — 조립기 직접 계측."""
    import _agents
    import _question
    from _assembler import assemble_requirements

    for name in ("Q1", "Q2", "Q4", "Q5"):
        q = QS[name][0]
        parsed = _question.parse_question(q)
        parsed.pop("needs_llm", None)
        assignments = _agents._assign_req_topics(parsed["requirements"])
        res = assemble_requirements(q, "2교시형(서술)", 25, parsed, assignments)
        body = res["body"]
        parts = re.split(r"(<h2[^>]*>.*?</h2>)", body, flags=re.S)
        counts, cur = [], None
        for p in parts:
            if p.startswith("<h2"):
                if cur is not None:
                    counts.append(cur)
                cur = 1
            elif cur is not None:
                cur += sum(_agents._block_lines(b) for b in _agents._split_blocks(p))
        counts.append(cur)
        req_counts = counts[1:1 + 3]
        for i, c in enumerate(req_counts):
            assert 12 <= c <= 19, (name, i, c, counts)  # 마지막 단락 결론 2줄 합류 허용


def test_deficit_placeholder_without_key():
    """부품 부족: 뼈대 토픽(TRIZ)에 절차를 묻는 변형 → 플레이스홀더 표 + reply 경고."""
    r = _pipeline("TRIZ의 개념을 설명하고 적용 절차를 제시하시오 (25점)")[-1]
    assert r.get("library") is True and r.get("llm_calls") == 0, r.get("reply", "")[:80]
    assert (r.get("question") or {}).get("requirements") == 2
    assert "미등록 부품" in r["artifact"]["html"]
    assert "미등록 부품" in r["reply"], r["reply"][:200]


def test_deficit_llm_write_with_key_mock():
    """부품 부족 + 키 설정 → 부족 요구 일괄 집필 1콜(합산 2콜 이내), 플레이스홀더 대체."""
    import httpx
    import _agents

    frag = ('<h3>적용 절차</h3><table class="t3"><thead><tr><th>단계</th><th>활동</th>'
            "<th>설명</th></tr></thead><tbody>"
            '<tr class="r2"><td>1단계</td><td>모순 정의</td><td>기술 모순 도출함</td></tr>'
            '<tr class="r2"><td>2단계</td><td>원리 적용</td><td>발명원리 매핑함</td></tr>'
            '<tr class="r2"><td>3단계</td><td>해결·검증</td><td>해결안 검증 적용함</td></tr>'
            "</tbody></table>")
    payload = '{"sections": [{"label": "R2", "html": "' + frag.replace('"', '\\"') + '"}]}'

    os.environ["GEMINI_API_KEY"] = "test-key"
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": payload}}]})

    orig = httpx.AsyncClient
    _agents.httpx.AsyncClient = lambda **kw: orig(transport=httpx.MockTransport(handler))
    try:
        async def run():
            return [e async for e in _agents.run_pipeline(
                [], "TRIZ의 개념을 설명하고 적용 절차를 제시하시오 (25점)")]
        evs = asyncio.run(run())
    finally:
        _agents.httpx.AsyncClient = orig
        os.environ.pop("GEMINI_API_KEY", None)

    r = evs[-1]
    assert r["llm_calls"] <= 2 and len(calls) == r["llm_calls"], (r["llm_calls"], calls)
    assert "미등록 부품" not in r["artifact"]["html"]
    assert "모순 정의" in r["artifact"]["html"]


def test_regression_simple_paths_untouched():
    """회귀: 단순형(N<=1)·복합 나열은 표준 구조 유지 + ITSM 견본 경고 0."""
    r = _pipeline("ITSM에 대하여 설명하시오 (25점)")[-1]
    assert r["matched"] == ["MG-001"] and r["review"]["warnings"] == []
    assert (r.get("question") or {}).get("form") == "simple"
    h2s = _h2s(r["artifact"]["html"])
    assert len(h2s) == 4 and "개요" in h2s[0]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)}개 테스트 전부 통과")
