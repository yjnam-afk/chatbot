"""문항 파서 단위 테스트 — 기출 표본 24문항 픽스처 (docs/question-spec.md 부록 A).

규칙 경로만으로 form·요구 수·순서가 기대값과 일치해야 한다 (기준 ≥22/24 —
LLM 폴백 판정 2건 이하). 실행: python3 tests/test_question.py  (LLM 없이)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

from _question import (  # noqa: E402
    accept_llm, clip_label, fallback_parse, parse_question, req_title,
)

# (번호, 문항 원문, 기대 form, 기대 요구 수, 요구별 핵심어(순서 검증), needs_llm 허용)
# 원문은 부록 A의 축약을 요구 구조 보존 형태로 복원한 것 (출처: 138회 해설집 · 방법론 V5.0)
SAMPLES = [
    (1, "상용 DBMS를 오픈소스 DBMS로 전환하고자 한다. 다음을 설명하시오. "
        "가. 전환 배경 나. 제약사항 다. 단계별 마이그레이션 절차 라. HA 아키텍처 구성 방안",
     "sub", 4, ["배경", "제약", "절차", "구성 방안"]),
    (2, "다음 내용을 설명하시오. 가. ISP의 정의 및 목적 나. ISP 수행방법론 체계와 절차 "
        "다. ISP, ISMP 비교",
     "sub", 3, ["정의", "절차", "비교"]),
    (3, "GPU와 TPU에 대하여 다음을 설명하시오. 가. GPU와 TPU의 개념 나. GPU와 TPU의 비교 "
        "다. TPU 사용 이유 라. TPU의 장점 및 향후 전망",
     "sub", 4, ["개념", "비교", "이유", "전망"]),
    (4, "B기관은 클라우드 네이티브 전환 사업을 추진하고 있다. 다음을 설명하시오. "
        "가. TA와 AA의 역할 비교(범위, 책임/목표, 주요 산출물 측면) 나. TA와 AA 협업의 중요성 및 방안",
     "sub", 2, ["비교", "협업"]),
    (5, "재공학과 역공학에 대하여 다음을 설명하시오. 가. 재공학의 개념 및 목적 "
        "나. 재공학의 절차 다. 역공학의 개념 및 활용 방안",
     "sub", 3, ["재공학의 개념", "절차", "역공학"]),
    (6, "메모리 구조 4개 영역의 역할, 특징, 동작 메커니즘을 설명하시오. "
        "가. 코드 나. 데이터 다. 힙 라. 스택",
     "sub", 4, ["코드", "데이터", "힙", "스택"]),
    (7, "생성형 AI 이용자 보호 가이드라인에 대하여 다음을 설명하시오. "
        "가. 제정 배경 및 필요성 나. 실행방식 4가지를 제시하고 각 방식이 중요한 이유를 설명하시오.",
     "sub", 3, ["배경", "실행방식", "이유"]),
    (8, "A사는 로우코드 플랫폼 적용을 검토하고 있다. 다음을 설명하시오. "
        "가. 로우코드의 주요 특징 나. 노코드와의 비교 다. 적용 시 한계",
     "sub", 3, ["특징", "비교", "한계"]),
    (9, "데이터베이스 분할에 대하여 다음을 설명하시오. 가. 분할 단위 나. 수평분할과 수직분할",
     "sub", 2, ["분할 단위", "수평분할"]),
    (10, "가용성 보장 방안에 대하여 다음을 설명하시오. 가. FTS 구현 방법 나. HA 구현 방법 "
         "다. FTS와 HA의 비교",
     "sub", 3, ["FTS", "HA", "비교"]),
    (11, "사이버 위협에 대하여 다음을 설명하시오. 가. 공격 표면과 공격 벡터 "
         "나. 측면 이동의 단계별 메커니즘을 설명하고 주요 기법을 제시하시오.",
     "sub", 3, ["공격 표면", "메커니즘", "기법"]),
    (12, "ISMS에 대하여 다음을 설명하시오. 가. 인증 영역과 법적 근거 나. 관리 과정 "
         "다. 간편인증 제도 라. 강화 방안",
     "sub", 4, ["인증 영역", "관리 과정", "간편인증", "강화 방안"]),
    (13, "공공기관은 민간 클라우드 활용을 확대하려고 한다. 다음을 설명하시오. "
         "가. 민간 클라우드 활용 절차의 태스크를 설명하고 기본설계 방안을 제시하시오. "
         "나. 4가지 활용구조를 설명하고 CSAP 인증 절차를 설명하시오. 다. 서비스 유형 및 평가기준",
     "sub", 5, ["태스크", "기본설계", "활용구조", "CSAP", "유형"]),
    (14, "자율주행 자동차에 적용된 센서 및 통신기술에 대하여 설명하시오. (2개 이상)",
     "simple", 1, ["센서"]),
    (15, "플랫폼에 대하여 다음을 설명하시오. 가. 플랫폼 전략과 플랫폼 비즈니스의 차이점 "
         "나. 플랫폼 전략의 주요 기능 다. 플랫폼 비즈니스의 유형 및 향후 방향",
     "sub", 3, ["차이점", "기능", "유형"]),
    (16, "ISO 21500에 대하여 다음을 설명하시오. 가. 범위관리 기획·통제 단계의 세부활동 "
         "나. WBS(정의, 주요투입물, 작성방법 등) 다. WBS 활용 사례 제시",
     "sub", 3, ["세부활동", "WBS", "사례"]),
    (17, "다음 데이터 마이닝 기법에 대하여 설명하시오. 1) K-means 2) DBSCAN 3) SVM",
     "sub", 3, ["K-means", "DBSCAN", "SVM"]),
    (18, "BCP에 대해 다음을 설명하시오. 가. ISO 22301의 주요 내용 "
         "나. BCP 달성을 위한 PDCA 사이클 적용 다. BIA 사례 적용",
     "sub", 3, ["ISO 22301", "PDCA", "BIA"]),
    (19, "소프트웨어 시험 기법에 대하여 다음을 설명하시오. 가. 블랙박스 시험 나. 화이트박스 시험",
     "sub", 2, ["블랙박스", "화이트박스"]),
    (20, "예비타당성조사 제도의 필요성, 주요기준, 문제점 및 개선방향에 대하여 설명하시오.",
     "inline", 4, ["필요성", "주요기준", "문제점", "개선방향"]),
    (21, "정부는 망중립성 법제화를 추진하고 있다. 다음을 설명하시오. "
         "가. 망중립성의 쟁점 사항 및 정책 이슈 나. 시사점 및 대응 방안",
     "sub", 2, ["쟁점", "시사점"]),
    (22, "XP(eXtreme Programming)의 특징 및 실천 방법에 대하여 설명하시오.",
     "inline", 2, ["특징", "실천 방법"]),
    (23, "정보공학 방법론에서 CBD로 전환하려고 한다. CBD의 요소기술 및 개발공정, "
         "문제점과 해결방안에 대하여 설명하시오.",
     "inline", 3, ["요소기술", "개발공정", "문제점"]),
    (24, "정보시스템 운영 성과측정 지표에 대하여 설명하시오. (비용측면, 업무측면)",
     "simple", 1, ["성과측정"]),
]


def test_samples_rule_path():
    """표본 24문: 규칙 경로 form·요구 수·순서 일치 ≥22, LLM 폴백 판정 ≤2."""
    fails, llm_flagged = [], []
    for no, q, form, n, keys, *_ in SAMPLES:
        p = parse_question(q)
        if p["needs_llm"]:
            llm_flagged.append(no)
            continue
        reqs = p["requirements"]
        ok = p["form"] == form and len(reqs) == n and all(
            k in reqs[i]["text"] for i, k in enumerate(keys))
        if not ok:
            fails.append((no, p["form"], [r["text"] for r in reqs]))
    assert len(llm_flagged) <= 2, f"LLM 폴백 판정 {len(llm_flagged)}건: {llm_flagged}"
    assert len(fails) == 0 and len(SAMPLES) - len(llm_flagged) >= 22, \
        f"규칙 경로 실패 {len(fails)}건: {fails}"


def test_text_is_substring_of_original():
    """모든 요구 text는 원문(공백 정규화)의 부분 문자열 — 목차 스탬프 계약."""
    import re
    for no, q, *_ in SAMPLES:
        hay = re.sub(r"\s+", "", q)
        for r in parse_question(q)["requirements"]:
            assert re.sub(r"\s+", "", r["text"]) in hay, (no, r["text"])


def test_scenario_detection():
    """시나리오 리드는 독립 문형이 아니라 수식 속성 — scenario 필드로 보존."""
    for no in (4, 8, 13, 21, 23):
        q = next(s[1] for s in SAMPLES if s[0] == no)
        p = parse_question(q)
        assert p["scenario"], f"#{no} 시나리오 미검출"
    # 순수 요구 리드는 시나리오가 아니다
    assert not parse_question(SAMPLES[1][1])["scenario"]  # #2


def test_sub_points_and_count():
    p4 = parse_question(SAMPLES[3][1])  # #4 괄호 측면 지정
    assert p4["requirements"][0]["sub_points"] == ["범위", "책임/목표", "주요 산출물"]
    p7 = parse_question(SAMPLES[6][1])  # #7 "4가지"
    assert p7["requirements"][1]["count"] == 4
    p14 = parse_question(SAMPLES[13][1])  # #14 "(2개 이상)"
    assert p14["requirements"][0]["count"] == 2
    p16 = parse_question(SAMPLES[15][1])  # #16 WBS(정의, 주요투입물, 작성방법 등)
    assert p16["requirements"][1]["sub_points"] == ["정의", "주요투입물", "작성방법"]
    p24 = parse_question(SAMPLES[23][1])  # #24 측면 지정
    assert p24["requirements"][0]["sub_points"] == ["비용", "업무"]


def test_verb_slot_tagging():
    p2 = parse_question(SAMPLES[1][1])  # #2 = Q1
    assert p2["requirements"][0]["slots"][0] == "definition_long"
    assert "procedure" in p2["requirements"][1]["slots"]
    assert p2["requirements"][2]["verb"] == "compare"
    p5q = parse_question("SWOT 분석의 구성요소를 설명하고 BCG Matrix와 비교한 후 "
                         "활용방안을 제시하시오.")  # Q5 (복동사 3분해)
    assert p5q["form"] == "inline" and len(p5q["requirements"]) == 3
    assert [r["verb"] for r in p5q["requirements"]] == ["explain", "compare", "propose"]
    assert p5q["requirements"][0]["slots"][0] == "components"
    assert p5q["requirements"][2]["slots"][0] == "usage"


def test_target_list_inherits_lead_triggers():
    p6 = parse_question(SAMPLES[5][1])  # #6 메모리 4영역 — 리드 트리거 복제
    for r in p6["requirements"]:
        assert set(r["slots"]) & {"features", "diagram", "definition_long"}, r


def test_terms_question_stays_simple():
    """1교시형(용어) 문항은 simple 수렴 — 기존 경로 무변경 (규칙 8)."""
    p = parse_question("SLA에 대하여 약술하시오. (10점)")
    assert p["form"] == "simple" and len(p["requirements"]) == 1
    assert not p["needs_llm"]


def test_fallback_parse():
    p = fallback_parse("FP 산정 문제를 풀이하시오. 복잡한 계산 조건 다수.")
    assert p["form"] == "simple" and p["parse"] == "fallback"
    assert len(p["requirements"]) == 1


def test_accept_llm_rejects_fabrication():
    q = SAMPLES[1][1]
    ok = accept_llm(q, '{"form":"sub","scenario":"","requirements":'
                       '[{"label":"가","text":"ISP의 정의 및 목적","verb":"define"}]}')
    assert ok and ok["parse"] == "llm"
    bad = accept_llm(q, '{"form":"sub","requirements":'
                        '[{"label":"가","text":"원문에 없는 요구사항"}]}')
    assert bad is None  # 원문에 없는 요구 생성 금지


def test_req_title_and_clip():
    p = parse_question(SAMPLES[1][1])
    assert req_title(p["requirements"][0]) == "ISP의 정의 및 목적"
    assert req_title(p["requirements"][2]) == "ISP, ISMP 비교"
    t = "BCP 달성을 위한 PDCA 사이클 적용"
    lab = clip_label(t)
    assert len(lab) <= 12 and lab in t


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)}개 테스트 전부 통과")
