# 픽셀 오피스 챗봇

멀티 에이전트 챗봇(harness-100 38-chatbot-builder 패턴) + 픽셀 오피스 시각화(Star-Office-UI 스타일). Vercel 서버리스 배포 대상.

## 실행

```bash
pip install -r requirements.txt
uvicorn api.index:app --port 8000
```

LLM 키(`GROQ_API_KEY` / `GEMINI_API_KEY` / `LLM_API_KEY`+`LLM_BASE_URL`+`LLM_MODEL`) 없으면 데모 모드. OpenAI 호환 chat/completions API 사용.

## 구조

- `api/_agents.py` — 5-에이전트 파이프라인. `run_pipeline(history, message)`은 **async generator**: `{type:"agent", agent, state, activity}` 이벤트들을 yield하고 마지막에 `{type:"reply", ...}` yield. 상태값: `idle|thinking|working|done|error`.
- `api/index.py` — FastAPI. `/api/chat`이 파이프라인 출력을 NDJSON으로 스트리밍. Vercel에서는 이 파일 하나가 서버리스 함수(vercel.json rewrites 참고), 로컬에서는 api/_static/도 마운트.
- `api/_static/office.js` — 캔버스 픽셀 렌더러. 에이전트 id ↔ 책상 위치 매핑(`DESKS`)이 `_agents.py`의 AGENTS id와 일치해야 함.
- `api/_static/app.js` — 채팅 + NDJSON 스트림 파싱. 대화 이력은 클라이언트가 유지(서버리스라 서버 세션 없음).

## 주의

- **서버 측 인메모리 상태 금지** — Vercel 함수는 호출 간 메모리를 공유하지 않는다. 이벤트는 응답 스트림에, 이력은 클라이언트에.
- 픽셀아트는 전부 코드로 렌더링 — 외부 이미지 에셋을 추가하지 말 것 (Star-Office-UI 에셋은 비상업 라이선스).
- 에이전트를 추가/변경할 때 `AGENTS`(_agents.py)와 `DESKS`(office.js) 양쪽을 함께 수정.
- `api/` 안에서 라우트로 노출되면 안 되는 모듈은 `_` 접두사 유지.
