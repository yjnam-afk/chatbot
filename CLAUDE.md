# 픽셀 오피스 챗봇

멀티 에이전트 챗봇(harness-100 38-chatbot-builder 패턴) + 픽셀 오피스 시각화(Star-Office-UI 스타일).

## 실행

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

`ANTHROPIC_API_KEY` 없으면 데모 모드(LLM 호출 없이 파이프라인 재현). 모델은 `CHATBOT_MODEL` 환경변수로 변경(기본 `claude-opus-4-8`).

## 구조

- `app/agents.py` — 5-에이전트 파이프라인. `run_pipeline(history, message, emit)`이 emit 콜백으로 상태를 방송. 상태값: `idle|thinking|working|done|error`.
- `app/main.py` — FastAPI. `EventHub`가 SSE 구독자에게 상태 이벤트 방송. 세션은 인메모리 dict.
- `static/office.js` — 캔버스 픽셀 렌더러. 에이전트 id ↔ 책상 위치 매핑(`DESKS`)이 `agents.py`의 AGENTS id와 일치해야 함.
- `static/app.js` — 채팅 + EventSource.

## 주의

- 픽셀아트는 전부 코드로 렌더링 — 외부 이미지 에셋을 추가하지 말 것 (Star-Office-UI 에셋은 비상업 라이선스).
- 에이전트를 추가/변경할 때 `AGENTS`(agents.py)와 `DESKS`(office.js) 양쪽을 함께 수정.
