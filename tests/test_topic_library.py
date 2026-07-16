"""매칭 엔진 단위 테스트 (LLM 없이 실행).

실행: python3 tests/test_topic_library.py  (또는 pytest tests/)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

from _topic_library import (  # noqa: E402
    choseong, match, norm, pick_from_candidates, stats,
)


def test_norm():
    assert norm("Zero Trust (제로 트러스트)!") == "zerotrust제로트러스트"
    assert norm("ㅅㅂㅅ ㅅㅈㅎㅇ") == "ㅅㅂㅅㅅㅈㅎㅇ"  # 자모 보존
    assert norm("") == ""


def test_choseong():
    assert choseong("제로트러스트") == "ㅈㄹㅌㄹㅅㅌ"
    assert choseong("서비스 수준 협약") == "ㅅㅂㅅㅅㅈㅎㅇ"
    assert choseong("SLA") == ""


def test_single_hit_sla():
    r = match("SLA에 대하여 설명하시오 (25점)")
    assert r["status"] == "hit" and r["matched"] == ["MG-004"], r


def test_alias_english():
    r = match("Service Level Agreement를 설명하시오")
    assert r["status"] == "hit" and r["matched"] == ["MG-004"], r


def test_alias_choseong():
    # 초성 별칭 단독 입력 → 완전 포함급 확정 (완료 기준: 초성 별칭 입력도 MG-004 적중)
    r = match("ㅅㅂㅅㅅㅈㅎㅇ에 대하여 설명하시오")
    assert r["status"] == "hit" and r["matched"] == ["MG-004"], r


def test_multi_hit_compare():
    r = match("SLA와 SLM을 비교하여 설명하시오 (25점)")
    assert r["status"] == "hit" and set(r["matched"]) == {"MG-004", "MG-005"}, r


def test_itsm_absorbs_duplicate():
    # MG-080 "ITSM (ISO/IEC 20000)"의 별칭 충돌이 해소되어 MG-001 단독 적중
    r = match("ITSM의 검토 모델과 성공적 구축 요건에 대하여 설명하시오 (25점)")
    assert r["status"] == "hit" and r["matched"] == ["MG-001"], r


def test_swot_and_drs():
    assert match("SWOT 분석에 대하여 설명하시오 (10점)")["matched"] == ["MG-024"]
    assert match("재해복구시스템(DRS)의 유형을 설명하시오 (25점)")["matched"] == ["MG-016"]


def test_miss():
    r = match("양자내성암호에 대하여 설명하시오 (25점)")
    assert r["status"] == "miss" and r["matched"] == [], r


def test_ambiguous_partial():
    # 이름 부분 토큰(+5)만 매치 → 3 <= score < 8 모호 구간
    r = match("협약이란 무엇인지 서술하시오")
    assert r["status"] == "ambiguous" and r["matched"] == [], r
    assert any(c["id"] == "MG-004" for c in r["candidates"]), r


def test_pick_from_candidates_blocks_hallucination():
    cands = [{"id": "MG-004", "name": "SLA", "definition": "", "score": 5}]
    assert pick_from_candidates(["MG-004", "MG-999", "없는ID"], cands) == ["MG-004"]
    assert pick_from_candidates([], cands) == []


def test_stats():
    s = stats()
    assert s["topics"] >= 166 and s["terms"] >= 500 and s["full_parts"] >= 5, s


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)}개 테스트 전부 통과")
