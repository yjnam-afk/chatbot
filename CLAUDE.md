# 기술사 답안 사무소 — 답안 작성 팀

기술사 시험 문제를 입력하면 **토픽 라이브러리 우선**으로 실물 답안지(줄 그리드 HTML, artifact)를 만들어주는 멀티 에이전트 서비스: 접수(규칙 판별) → 토픽 검색(문항 파싱+무LLM 매칭) → 답안 편집(부품 조립) → 집필(접합부) → 검증(형식 린트). 라이브러리 적중 시 **LLM 0~2콜·3초 이내, 키 없이도 실답안**. 미적중만 라이브 파이프라인 폴백(설계→작성→채점, 85점 미만/형식 위반 시 1회 보완+재채점, 최대 5콜). 과정은 진행 3단계 레일로 시각화. Vercel 서버리스 배포 대상.

2교시형 문항은 **요구사항 파싱 우선** (발주 원칙: "물어본 요구만, 물어본 순서대로, 목차는 지문 원문 어구"): 소문항(가나다)·복동사·인라인 나열·괄호 지정을 무LLM 규칙으로 분해(docs/question-spec.md)하고, 요구 2건 이상이면 요구 순서 = 단락 순서(Ⅱ~Ⅵ 가변)로 조립하며 서론 로드맵(d7)이 물어본 항목을 그린다.

## 자율 개발 팀으로 쓰기 (Claude Code)

이 레포에는 `.claude/agents/`에 동일한 팀 구성의 Claude Code 서브에이전트가 정의되어 있다:
planner(누리·기획) / designer(다인·디자인) / developer(로운·개발) / qa(세아·QA).
메인 세션(당신)이 팀장 '코디' 역할이다.

큰 작업을 받으면 이 사이클로 자율 진행한다:

1. **planner**로 스펙/완료 기준 확정 → `TODO.md`에 마일스톤 기록
2. 마일스톤마다: **designer**(화면 있는 작업만) → **developer**(구현+실행 확인+커밋) → **qa**(완료 기준 검증)
3. qa 반려 시 developer로 돌아가 수정. 통과 시 `TODO.md` 체크 후 다음 마일스톤
4. 전체 완료 기준 충족까지 사용자에게 묻지 않고 반복한다. 진행 상황은 커밋 로그가 말하게 한다.

## 실행

```bash
pip install -r requirements.txt
uvicorn api.index:app --port 8000
```

LLM 키(`GEMINI_API_KEY`(권장) / `GROQ_API_KEY` / `LLM_API_KEY`+`LLM_BASE_URL`+`LLM_MODEL`) 없으면 폴백 경로만 데모 모드 — **라이브러리 적중 경로는 키 없이도 실답안**. OpenAI 호환 chat/completions API 사용.

## 구조

- `api/_agents.py` — 파이프라인. `run_pipeline(history, message, kind_hint)`은 **async generator**: `{type:"agent", agent, state, activity}`/`{type:"talk"}` 이벤트를 yield하고 마지막에 `{type:"reply", library, matched, llm_calls, sheet, ...}` yield. 답안지 줄 그리드 템플릿(`_ANSWER_TEMPLATE`·`render_answer`, docs/answer-template-spec.md)과 분량 모델(`_volume` — 쪽·줄), 형식 린터(`_lint_format`)도 여기.
- `api/_question.py` — 2교시 문항 파서(무LLM 규칙 우선, docs/question-spec.md 2-1): 마커 분리→대상 나열 판별→복동사 분해→인라인 나열→괄호·개수→동사·슬롯 태깅. 요구 text는 항상 원문 부분 문자열(목차 스탬프 계약). LLM 폴백(1콜)은 _agents가 llm_messages/accept_llm으로 호출·검증.
- `api/_topic_library.py` — 토픽 라이브러리: 정규화(norm/초성), 무LLM 매칭 점수제(docs/library-spec.md 2-3), index/topics 정적 로딩(콜드스타트 캐시 — 읽기 전용이라 무상태 원칙과 무충돌).
- `api/_assembler.py` — 부품→답안 본문 조립기: 표준 구조(단순형·1교시·복합 나열 — `assemble`)와 요구 주도 조립(`assemble_requirements` — 요구별 토픽 배정·슬롯 대체 강등·분량 예산·동적 d7 로드맵·부족 요구 deficits), 자동 대비표.
- `api/_library/` — `topics/MG-*.json`(토픽 부품, 스키마는 `schema.md`) + `index.json`(**빌드 산출물** — 직접 수정 금지).
- `scripts/seed_from_xlsx.py`(뼈대 생성, 부품 보존 머지) / `scripts/build_index.py`(스키마 검증+인덱스 재생성 — **토픽 수정 커밋 전 반드시 실행**, 실패 시 exit 1).
- `api/index.py` — FastAPI. `/api/chat`(NDJSON 스트림), `/api/agents`, `/api/library`(통계+서랍 목록). Vercel에서는 이 파일 하나가 서버리스 함수, 로컬에서는 api/_static/도 마운트.
- `api/_static/` — `progress.js`(이벤트→진행 3단계 매핑), `stage.js`(iframe 답안지·게이지·인쇄), `drawer.js`(토픽 서랍), `app.js`(스트림 파싱·대화 도크). 대화 이력은 클라이언트가 유지.
- `tests/` — 전부 LLM 없이 실행: `test_topic_library.py`(매칭), `test_question.py`(파서 — 표본 24문 픽스처), `test_assembler.py`(표준 조립·라우팅), `test_requirements_e2e.py`(question-spec 3장 완료 기준 Q1~Q5).

## 주의

- **서버 측 인메모리 상태 금지** — Vercel 함수는 호출 간 메모리를 공유하지 않는다. 이벤트는 응답 스트림에, 이력은 클라이언트에. (라이브러리 로딩은 읽기 전용 콜드스타트 캐시라 예외)
- 에이전트를 추가/변경할 때 `AGENTS`(_agents.py)와 `progress.js`의 `AGENT2STEP`을 함께 수정. 역할 표기는 `/api/agents`의 role이 단일 출처 — 프런트에 role 문자열 하드코딩 금지.
- 토픽 JSON을 수정하면 `python3 scripts/build_index.py`로 검증+인덱스 재생성 후 함께 커밋. HTML 부품에 `<script>`/외부 URL 금지(빌드가 차단).
- `api/` 안에서 라우트로 노출되면 안 되는 모듈은 `_` 접두사 유지.
- 답안 본문은 줄 그리드 계약(docs/answer-template-spec.md §7)을 따른다 — 허용 태그·클래스 밖 마크업 금지.
