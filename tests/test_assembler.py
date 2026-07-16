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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)}개 테스트 전부 통과")
