# TODO — 토픽 라이브러리 기반 답안지 즉시 조립

스펙: `docs/library-spec.md` (누리, 2026-07-16). 템플릿: `docs/answer-template-spec.md` (다인, 작성 중).
방향: 토픽 부품 사전 작성 → 무LLM 매칭 → 조립. 적중 시 LLM 0~1콜·3초 내, 미적중만 기존 파이프라인 폴백.

## 마일스톤

- [x] **M1 — 답안지 템플릿 재설계** (다인 스펙 확정 → 로운 구현 완료)
  - [x] `docs/answer-template-spec.md`: 1교시형/2교시형 섹션 슬롯 정의 (강정배 방법론 기반)
  - [x] 슬롯 인터페이스 확정: §7 프래그먼트 계약 (ans/def/gloss/diagram(.d7)/t3·t2·tcmp·texp/r2)
  - [x] `_ANSWER_TEMPLATE`·`render_answer` 줄 그리드(v1 연속 본문)로 교체, `_volume` 쪽·줄 기반 갱신, 데모 답안 새 계약 적용
- [x] **M2 — 라이브러리 데이터 모델·씨앗** (스펙 2-1, 2-2)
  - [x] `api/_library/schema.md` + 토픽 JSON 스키마 확정
  - [x] `scripts/seed_from_xlsx.py`: 경영전략_토픽 166건 → 뼈대 JSON (부품 보존 머지, 별칭 충돌 해소)
  - [x] `scripts/build_index.py`: 스키마 검증(+HTML 금칙어, 총량 5MB, comparisons 참조 무결성) → `index.json` 생성
  - [x] 대표 5토픽 풀부품 수작업: ITSM(MG-001)/SLA(MG-004)/SLM(MG-005)/DRS(MG-016)/SWOT(MG-024), SLA↔SLM 비교 상호참조
- [x] **M3 — 매칭 엔진** (스펙 2-3)
  - [x] `api/_topic_library.py`: norm(초성/약어), 점수화(10/6/5/1), 확정 임계값 8, 복수 매칭(최대 3), 긴 term 흡수, 초성 완전어 확정 보강
  - [x] 모호 구간 후보 반환 + `pick_from_candidates`(후보 밖 id 차단 — LLM 1콜 선택은 M4 파이프라인에서 연결), 단위 테스트 12건
- [x] **M4 — 조립 파이프라인** (스펙 2-4, 2-6)
  - [x] 단일/복합/부분적중 조립(api/_assembler.py), 자동 대비표, 암기 박스 병렬, LIBRARY_POLISH 옵션
  - [x] run_pipeline 분기 통합: 적중→조립(0~2콜) / 미적중→라이브 폴백(최대 5콜, +모호 1콜) / 일반 질문→chat(1콜)
  - [x] 세아 규칙 검증(_verify_assembly: 필수 슬롯·표 2행·암기박스·외부리소스 + 형식 린트), 5인 연출(sleep ≤0.15s, 실측 0.7초)
- [x] **M5 — 프런트/라우트** (스펙 2-5 + front-ui-spec.md 시안 A+B)
  - [x] 에이전트 역할 재정의: 접수/토픽 검색/답안 편집/집필/검증 — office.js 삭제, AGENTS↔progress.js AGENT2STEP 동기화, role 하드코딩 제거
  - [x] `GET /api/library` 통계+서랍 목록 라우트, 결과 메타 라인 "라이브러리 적중 N건 · LLM n콜 · n초"
  - [x] 프론트 재작성: 헤더 입력줄+교시형 칩 / 레일(진행 3단계·분량 게이지·내보내기·토픽 서랍) / 무대(iframe 답안지) / 대화 도크
  - [x] 풀부품 15건 추가 (누적 20) — kpc/ITPE 기출 빈도 기준 (CSF/KPI는 토픽 부재로 IT Governance 대체)
- [x] **M6 — QA·문서화**
  - [x] `docs/library-spec.md` 3장 완료 기준 curl 전항목 검증 — 단일 적중 0.72초 실측, 키 필요 항목(모호 llm<=1) 제외 전부 통과
  - [x] CLAUDE.md·README.md 갱신 (라이브러리 우선 아키텍처, office.js→progress.js 동기화 규칙, build_index 필수 규칙)

## 2교시 문항 구조 대응 (스펙: `docs/question-spec.md`, 누리 2026-07-17)

발주 근거: "2교시 문제는 단순하지가 않은데" — 소문항(가나다)·복동사·괄호 지정·대상 나열을
파싱해 요구 순서대로 단락을 조립하고, 서론 로드맵이 "물어본 것"을 그리게 한다.

- [x] **M7 — 문항 파서** (question-spec 2-1)
  - [x] `api/_question.py`: 마커 분리/대상 나열 판별/복동사 분해/인라인 나열/괄호·개수 추출
  - [x] LLM 폴백 1콜 (요구 text 원문 부분 문자열 검증, 키 없으면 simple 강등)
  - [x] 스키마 v1.1: `procedure` 부품 추가 + build_index 검증 확장
  - [x] `tests/test_question.py`: 부록 A 표본 24문 픽스처, 규칙 경로 24/24 (기준 ≥22)
- [x] **M8 — 요구 기반 조립** (question-spec 2-2, 2-4, 2-5)
  - [x] `_assembler.py`: 요구→단락 가변(Ⅱ~Ⅵ), 동사·트리거→부품 슬롯 매핑, 대체 순서, 압축/확장 렌더
  - [x] 분량 재배분 (N=1~2: 1쪽 / 3: 0.7쪽 / 4~5: 0.5쪽, 요구당 최소 7줄 — 단락 예산으로 구현)
  - [x] 부족 슬롯 일괄 집필 1콜 — 파서 폴백·모호 매칭 합산 총 2콜 상한 유지 (§7 화이트리스트 검증)
- [x] **M9 — 동적 로드맵 서론** (question-spec 2-3)
  - [x] d7 로드맵 생성기: d-box = 단락 제목 12자 절삭, 박스 텍스트 ⊂ 해당 h2 제목
  - [x] 서론 타입 선택 규칙 (요구 2+ → Type IV / 0~1 → Type I / 보안 → Type III 리드문)
  - [x] 세아 검증 확장(_verify_requirements): 박스-h2 일치, 요구 순서, 비교→tcmp·절차→절차표
  - [x] `answer-template-spec.md` §5 v1.1 개정 (다인 협의 — 단락 수 가변 명시)
- [x] **M10 — QA·풀부품 보강**
  - [x] question-spec 3장 완료 기준 전항목 — tests/test_requirements_e2e.py + 서버 curl
        (Q1~Q5 키 없이 llm 0콜·0.7초·3~4쪽, 부족 슬롯 플레이스홀더/집필 목, 회귀 4스위트)
  - [x] 풀부품 보강 8건: ISP·ISMP(정의)·PDCA·성과측정지표·마이닝 3종·BCG + procedure 3건
        (ISP·DRS·ISO 22301 — ISMS는 MG 라이브러리에 토픽 부재로 대상 제외), CLAUDE.md 갱신

## 이후 확장 (이번 범위 아님)

- MG_000 취합본 나머지 146건 풀부품화 (배치 작업)
- MG 외 과목 라이브러리, 임베딩 검색, 사용자 커스텀 두문자
- 서론 타입 V·VI 전용 렌더, 계산·풀이형 문항, 시나리오 지문 심층 반영(LIBRARY_POLISH 확장)
