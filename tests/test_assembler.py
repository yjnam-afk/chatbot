"""조립기 단위 테스트 — split_subjects 오분리 회귀 (LLM 없이 실행).

실행: python3 tests/test_assembler.py  (또는 pytest tests/)
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

from _assembler import split_subjects  # noqa: E402


def _run_pipeline(msg):
    for k in ("GROQ_API_KEY", "GEMINI_API_KEY", "LLM_API_KEY"):
        os.environ.pop(k, None)
    import _agents

    async def run():
        return [e async for e in _agents.run_pipeline([], msg)]

    return asyncio.run(run())


def test_split_drops_generic_fragments():
    # "성과 관리와" 의 "과 " 오분리 잔여물("성", "관리")이 주제어로 새지 않아야 함
    assert split_subjects("성과 관리와 BSC를 비교 설명하시오 (25점)") == ["BSC"]


def test_split_normal_single():
    assert split_subjects("SLA의 개념, 구성요소, 활용방안에 대하여 설명하시오") == ["SLA"]


def test_split_normal_multi():
    assert split_subjects("SLA와 SLM을 비교하여 설명하시오 (25점)") == ["SLA", "SLM"]


def test_no_garbage_missing_section():
    # 재현 케이스: 쓰레기("관리의 개요 / 라이브러리 미등록 토픽") 소단락이 없어야 함
    evs = _run_pipeline("성과 관리와 BSC를 비교 설명하시오 (25점)")
    r = evs[-1]
    assert r["matched"] == ["MG-062"], r.get("matched")
    assert "라이브러리 미등록 토픽" not in r["artifact"]["html"]
    assert "관리의 개요" not in r["artifact"]["html"]


def test_multi_hit_still_works():
    evs = _run_pipeline("SLA와 SLM을 비교하여 설명하시오 (25점)")
    assert sorted(evs[-1]["matched"]) == ["MG-004", "MG-005"]


def test_lehman_verbatim_restore():
    """MG-167 리만 — 발주자 실물 손답안 1:1 복원 (반려 2026-07-21: 재해석 금지).

    단락 제목·표 열 이름·행 카테고리 병합·간글 문구가 손답안 원문과 일치해야 한다.
    """
    import re
    evs = _run_pipeline("리만의 소프트웨어 변화 원리에 대하여 설명하시오 (10점)")
    r = evs[-1]
    assert r["matched"] == ["MG-167"], r.get("matched")
    html = r["artifact"]["html"]
    text = re.sub(r"<[^>]+>", "", html)
    # 단락 제목 (Ⅰ 정의형 — 개요 아님 / Ⅱ 분류+8개 법칙 / Ⅲ 적용)
    assert "SW의 진화 법칙, 리만의 SW 변화 원리의 정의" in text
    assert "리만의 SW 분류 및 8개 법칙" in text
    assert "리만의 SW 분류" in text
    assert "리만 SW의 8개 법칙" in text
    assert "리만 SW 변화 원리 적용" in text
    # 표 열 이름 원문 (구분|관점|설명 · 구분|법칙|설명 · 구분|원리|적용)
    heads = [re.findall(r"<th>(.*?)</th>", th)
             for th in re.findall(r"<thead>(.*?)</thead>", html, re.S)]
    assert ["구분", "관점", "설명"] in heads, heads
    assert ["구분", "법칙", "설명"] in heads, heads
    assert ["구분", "원리", "적용"] in heads, heads
    # 8개 법칙 rowspan — 프로그램 5행·시스템 2행 병합
    assert re.search(r'rowspan="5"[^>]*>프로그램', html), "프로그램 5행 병합 누락"
    assert re.search(r'rowspan="2"[^>]*>시스템', html), "시스템 2행 병합 누락"
    # 간글 원문
    assert "– 리만 SW는 E-Type에 대한 변화원리 제시" in text
    assert "ISO/IEC 14764 기반, SW 변화" in text
    # SW 3분류 행
    for row in ("S-Type", "진화 불요", "P-Type", "부분 진화", "E-Type", "지속 진화"):
        assert row in text, row


def test_question_on_rule_lines_no_strip():
    """문제 스트립 폐지 (발주자 반려 2026-07-21) — 요약 전사가 괘선 첫 줄에 '문)' 거터로
    표기되고, 용지 안에 교시·배점 배지가 없어야 한다(배지는 용지 밖 메타 한 줄)."""
    import re
    evs = _run_pipeline("SLA에 대하여 설명하시오 (25점)")
    html = evs[-1]["artifact"]["html"]
    assert "q-strip" not in html
    m = re.search(r'<p class="q[^"]*"><span class="gut">문\)</span>', html)
    assert m, "문제 전사(p.q + 문) 거터) 누락"
    # 전사 → 답) 순서: 첫 페이지 content에서 q가 ans보다 앞
    assert html.find('<p class="q') < html.find('class="ans"')
    # 배지는 용지 밖 sheet-meta 한 줄(인쇄 제외), .page 내부에는 없음
    assert '<div class="sheet-meta"><span>' in html
    page0 = html[html.find('<div class="page">'):]
    assert "교시형" not in page0.split('</div>')[0]
    # 페이지 수 = reply.sheet.pages (전사 포함 계측 정합)
    assert html.count('<div class="page">') == evs[-1]["sheet"]["pages"]


def test_routing_answer_first():
    """답안지 우선 라우팅 — 구어체 답안 요청·토픽명 단독은 시험 경로, 의문문만 채팅."""
    import _agents
    assert _agents.is_exam_request("정규화 답안지 적어줘")
    assert _agents.is_exam_request("BIA 답안 만들어줘")
    assert _agents.is_exam_request("SLA")  # 짧은 입력 + 라이브러리 확정 적중
    assert _agents.is_exam_request("7S 모범답안")
    assert _agents.is_exam_request("2교시 문제 하나")
    assert _agents.is_exam_request("아무 주제나", kind_hint="2교시형")  # 칩 수동 선택 강제
    assert not _agents.is_exam_request("RTO랑 RPO 차이가 뭐야?")
    assert not _agents.is_exam_request("기술사 공부 어떻게 시작해?")


def test_routing_pipeline_e2e():
    r = _run_pipeline("BIA 답안 만들어줘")[-1]
    assert r["matched"] == ["MG-019"] and r["artifact"], r.get("matched")
    r = _run_pipeline("SLA")[-1]
    assert r["matched"] == ["MG-004"] and r["artifact"], r.get("matched")
    r = _run_pipeline("7S 모범답안")[-1]
    assert r["matched"] == ["MG-023"] and r["artifact"], r.get("matched")
    # 대화형 의문문 → 챗봇 답변 (무키 + 적중: 라이브러리 데이터 무LLM 답변 + 답안지 유도)
    r = _run_pipeline("RTO랑 RPO 차이가 뭐야?")[-1]
    assert "artifact" not in r and "exam" not in r
    assert r.get("library") is True and r.get("llm_calls") == 0
    assert "정의" in r["reply"] and "설명하시오" in r["reply"]


def test_miss_no_fake_sheet():
    """무키 미적중 → 질문과 무관한 고정 답안지 대신 정직한 안내 (발주자 피드백)."""
    r = _run_pipeline("양자내성암호에 대하여 설명하시오 (25점)")[-1]
    assert "artifact" not in r, "미적중에서 가짜 답안지가 나옴"
    assert r["library"] is False and r.get("demo") is True
    assert "라이브러리에 없어요" in r["reply"] and "토픽 서랍" in r["reply"]
    assert "GEMINI_API_KEY" in r["reply"]


def test_miss_suggests_candidates():
    """모호 점수(3~7) 후보가 있으면 '혹시 OO 말씀이세요?' 제안."""
    r = _run_pipeline("협약이란 무엇인지 서술하시오")[-1]
    assert "artifact" not in r
    assert "혹시" in r["reply"] and "SLA" in r["reply"], r["reply"]


def test_explicit_demo_only():
    """고정 데모 시나리오는 명시적 '데모' 입력 전용."""
    r = _run_pipeline("데모")[-1]
    assert r.get("demo") is True and r.get("artifact"), r.get("reply", "")[:60]
    art = r["artifact"]["html"]
    assert "정의" in art and "암기" in art
    assert r["review"]["score"] == 91 and r["review"]["rounds"] == 1


def test_stub_sheet_notice_first():
    """뼈대 적중+무키 반쪽 답안은 reply 첫 줄에 요약본 경고.

    (픽스처 토픽: TRIZ(MG-028) — 종전 PDCA(MG-069)는 M10에서 풀부품 승격)
    """
    r = _run_pipeline("TRIZ에 대하여 설명하시오 (25점)")[-1]
    assert r["reply"].startswith("⚠️"), r["reply"][:60]
    assert "요약본" in r["reply"].splitlines()[0]


_MOCK_CORE = (
    "<h2>TRIZ의 구성도 및 구성요소</h2>"
    "<h3>TRIZ의 구성도</h3>"
    '<div class="diagram"><div class="d-box">모순 정의</div><span class="d-arrow">→</span>'
    '<div class="d-box d-hub">40 발명원리</div><span class="d-arrow">→</span>'
    '<div class="d-box">해결안</div><span class="d-arrow">→</span>'
    '<div class="d-box soft">검증</div></div>'
    '<p class="gloss">– 모순을 원리로 해소하는 절차임</p>'
    "<h3>TRIZ의 구성요소</h3>"
    '<table class="t3"><thead><tr><th>구분</th><th>구성요소</th><th>설명</th></tr></thead>'
    '<tbody><tr class="r2"><td>분석</td><td>모순 행렬</td><td>기술 모순 유형화함</td></tr>'
    '<tr class="r2"><td>원리</td><td>발명원리</td><td>40개 해결 원리 적용함</td></tr>'
    '<tr class="r2"><td>진화</td><td>진화 법칙</td><td>시스템 발전 방향 예측함</td></tr>'
    '<tr class="r2"><td>도구</td><td>ARIZ</td><td>복합 문제 해결 알고리즘</td></tr></tbody></table>'
)


def test_skeleton_hit_llm_enrichment_mock():
    """뼈대 토픽 적중 + 키 있음 → 핵심 섹션 LLM 1콜 보강 (MockTransport, 실키 불필요)."""
    import httpx
    import _agents

    os.environ["GEMINI_API_KEY"] = "test-key"
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": _MOCK_CORE}}]})

    orig = httpx.AsyncClient
    _agents.httpx.AsyncClient = lambda **kw: orig(transport=httpx.MockTransport(handler))
    try:
        async def run():
            return [e async for e in _agents.run_pipeline([], "TRIZ에 대하여 설명하시오 (25점)")]
        evs = asyncio.run(run())
    finally:
        _agents.httpx.AsyncClient = orig
        os.environ.pop("GEMINI_API_KEY", None)

    r = evs[-1]
    assert r["matched"] == ["MG-028"], r.get("matched")
    assert r["llm_calls"] == 1 and len(calls) == 1, (r.get("llm_calls"), calls)
    art = r["artifact"]["html"]
    assert "TRIZ의 구성도 및 구성요소" in art and "d-hub" in art
    assert not any("Ⅱ단락 생략" in w for w in r["review"]["warnings"]), r["review"]["warnings"]


def test_skeleton_hit_without_key_keeps_warning():
    """뼈대 토픽 적중 + 키 없음 → 현행 유지 (Ⅱ단락 생략 경고)."""
    evs = _run_pipeline("TRIZ에 대하여 설명하시오 (25점)")
    r = evs[-1]
    assert r["matched"] == ["MG-028"] and r["llm_calls"] == 0
    assert any("Ⅱ단락 생략" in w for w in r["review"]["warnings"]), r["review"]["warnings"]


def test_composite_with_skeleton_no_wasted_call():
    """복합 적중에 뼈대 토픽이 섞여도 보강 콜을 쓰지 않는다 — 복합 조립기는 core를
    사용하지 않으므로 콜이 낭비되고 로운 talk이 허위가 되던 문제 (세아 반려 3)."""
    import httpx
    import _agents

    os.environ["GEMINI_API_KEY"] = "test-key"
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": _MOCK_CORE}}]})

    orig = httpx.AsyncClient
    _agents.httpx.AsyncClient = lambda **kw: orig(transport=httpx.MockTransport(handler))
    try:
        async def run():
            # TRIZ(MG-028, 뼈대) + SWOT(MG-024, 풀부품) 복합
            return [e async for e in _agents.run_pipeline(
                [], "TRIZ와 SWOT 분석을 비교하여 설명하시오 (25점)")]
        evs = asyncio.run(run())
    finally:
        _agents.httpx.AsyncClient = orig
        os.environ.pop("GEMINI_API_KEY", None)

    r = evs[-1]
    assert sorted(r["matched"]) == ["MG-024", "MG-028"], r.get("matched")
    assert r["llm_calls"] == 0 and not calls, (r.get("llm_calls"), calls)  # 콜 낭비 없음
    writer_talks = [e["text"] for e in evs if e.get("type") == "talk" and e.get("agent") == "writer"]
    assert not any("핵심 섹션" in t and "집필했어요" in t for t in writer_talks), writer_talks  # talk 진실성


def test_korean_hygiene_guard():
    """LLM 출력 한자·가나 혼입 → 금지 지시 강조 1회 재요청 → 정상 수용 (발주자 실사례)."""
    import httpx
    import _agents

    os.environ["GEMINI_API_KEY"] = "test-key"
    calls = []

    def handler(request):
        calls.append(request.read().decode())
        content = ("프로세스가 奠定하였다며 개선되어があり며 진행" if len(calls) == 1
                   else "프로세스 표준화로 개선함")
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    orig = httpx.AsyncClient
    _agents.httpx.AsyncClient = lambda **kw: orig(transport=httpx.MockTransport(handler))
    try:
        out = asyncio.run(_agents._chat("테스트", [{"role": "user", "content": "질문"}]))
    finally:
        _agents.httpx.AsyncClient = orig
        os.environ.pop("GEMINI_API_KEY", None)

    import json as _json
    assert len(calls) == 2, calls  # 혼입 → 1회 재요청
    sys2 = _json.loads(calls[1])["messages"][0]["content"]
    assert "금지" in sys2, "재요청에 금지 지시 강조 누락"
    assert out == "프로세스 표준화로 개선함"
    assert not _agents._FOREIGN_RE.search(out)
    # 재요청도 혼입이면 세정 사용
    assert _agents._scrub_foreign("개선되어があり며 奠定하였다") == "개선되어며 하였다"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)}개 테스트 전부 통과")
