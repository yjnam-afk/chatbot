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


_MOCK_CORE = (
    "<h2>PDCA의 구성도 및 구성요소</h2>"
    "<h3>PDCA의 구성도</h3>"
    '<div class="diagram"><div class="d-box">Plan</div><span class="d-arrow">→</span>'
    '<div class="d-box d-hub">Do</div><span class="d-arrow">→</span>'
    '<div class="d-box">Check</div><span class="d-arrow">→</span>'
    '<div class="d-box soft">Act</div></div>'
    '<p class="gloss">– 계획-실행-평가-개선의 순환 사이클임</p>'
    "<h3>PDCA의 구성요소</h3>"
    '<table class="t3"><thead><tr><th>구분</th><th>구성요소</th><th>설명</th></tr></thead>'
    '<tbody><tr class="r2"><td>계획</td><td>Plan</td><td>목표·프로세스 수립함</td></tr>'
    '<tr class="r2"><td>실행</td><td>Do</td><td>계획 이행·데이터 수집함</td></tr>'
    '<tr class="r2"><td>평가</td><td>Check</td><td>결과 측정·목표 대비 분석함</td></tr>'
    '<tr class="r2"><td>개선</td><td>Act</td><td>표준화·차기 계획 반영함</td></tr></tbody></table>'
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
            return [e async for e in _agents.run_pipeline([], "PDCA에 대하여 설명하시오 (25점)")]
        evs = asyncio.run(run())
    finally:
        _agents.httpx.AsyncClient = orig
        os.environ.pop("GEMINI_API_KEY", None)

    r = evs[-1]
    assert r["matched"] == ["MG-069"], r.get("matched")
    assert r["llm_calls"] == 1 and len(calls) == 1, (r.get("llm_calls"), calls)
    art = r["artifact"]["html"]
    assert "PDCA의 구성도 및 구성요소" in art and "d-hub" in art
    assert not any("Ⅱ단락 생략" in w for w in r["review"]["warnings"]), r["review"]["warnings"]


def test_skeleton_hit_without_key_keeps_warning():
    """뼈대 토픽 적중 + 키 없음 → 현행 유지 (Ⅱ단락 생략 경고)."""
    evs = _run_pipeline("PDCA에 대하여 설명하시오 (25점)")
    r = evs[-1]
    assert r["matched"] == ["MG-069"] and r["llm_calls"] == 0
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
            # PDCA(MG-069, 뼈대) + SWOT(MG-024, 풀부품) 복합
            return [e async for e in _agents.run_pipeline(
                [], "PDCA와 SWOT 분석을 비교하여 설명하시오 (25점)")]
        evs = asyncio.run(run())
    finally:
        _agents.httpx.AsyncClient = orig
        os.environ.pop("GEMINI_API_KEY", None)

    r = evs[-1]
    assert sorted(r["matched"]) == ["MG-024", "MG-069"], r.get("matched")
    assert r["llm_calls"] == 0 and not calls, (r.get("llm_calls"), calls)  # 콜 낭비 없음
    writer_talks = [e["text"] for e in evs if e.get("type") == "talk" and e.get("agent") == "writer"]
    assert not any("핵심 섹션" in t and "집필했어요" in t for t in writer_talks), writer_talks  # talk 진실성


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)}개 테스트 전부 통과")
