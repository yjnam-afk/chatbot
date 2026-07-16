# 기술사 답안 사무소 — 답안 작성 팀

멀티 에이전트 답안 작성 팀(harness-100 패턴): 기술사 시험 문제를 입력하면 출제 의도 분석→답안 구조 설계→작성→채점(85점 미만 시 1회 보완+재채점)을 거쳐 인쇄 가능한 답안지 HTML(artifact)을 만들어준다. 과정은 팀 보드 UI(카드/상태 칩/말풍선/진행선)로 실시간 시각화. Vercel 서버리스 배포 대상.

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

LLM 키(`GROQ_API_KEY` / `GEMINI_API_KEY` / `LLM_API_KEY`+`LLM_BASE_URL`+`LLM_MODEL`) 없으면 데모 모드. OpenAI 호환 chat/completions API 사용.

## 구조

- `api/_agents.py` — 5-에이전트 파이프라인. `run_pipeline(history, message)`은 **async generator**: `{type:"agent", agent, state, activity}` 이벤트들을 yield하고 마지막에 `{type:"reply", ...}` yield. 상태값: `idle|thinking|working|done|error`.
- `api/index.py` — FastAPI. `/api/chat`이 파이프라인 출력을 NDJSON으로 스트리밍. Vercel에서는 이 파일 하나가 서버리스 함수(vercel.json rewrites 참고), 로컬에서는 api/_static/도 마운트.
- `api/_static/office.js` — 팀 보드 렌더러(DOM). EMOJI/FLOW의 에이전트 id가 `_agents.py`의 AGENTS id와 일치해야 함.
- `api/_static/app.js` — 채팅 + NDJSON 스트림 파싱. 대화 이력은 클라이언트가 유지(서버리스라 서버 세션 없음).

## 주의

- **서버 측 인메모리 상태 금지** — Vercel 함수는 호출 간 메모리를 공유하지 않는다. 이벤트는 응답 스트림에, 이력은 클라이언트에.
- 에이전트를 추가/변경할 때 `AGENTS`(_agents.py)와 `DESKS`(office.js) 양쪽을 함께 수정.
- `api/` 안에서 라우트로 노출되면 안 되는 모듈은 `_` 접두사 유지.
