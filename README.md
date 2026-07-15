# 🏢 픽셀 오피스 챗봇 (Pixel Office Chatbot)

멀티 에이전트 팀이 협업해서 답변을 만들고, 그 과정을 **픽셀아트 오피스**로 실시간 시각화하는 챗봇입니다.

- 에이전트 팀 구성은 [harness-100](https://github.com/revfactory/harness-100)의 `38-chatbot-builder` 하네스 패턴(대화설계 → NLU → 통합 → 테스트, 5인 팀)을 따랐습니다.
- 시각화는 [Star-Office-UI](https://github.com/ringhyacinth/Star-Office-UI)에서 영감을 받았으며, 라이선스 문제가 없도록 모든 픽셀아트를 캔버스 코드로 직접 렌더링합니다(외부 에셋 없음).

## 에이전트 팀

| 에이전트 | 역할 | 하는 일 |
|---|---|---|
| 🟡 코디 | 오케스트레이터 | 턴 시작/종료, 작업 분배 |
| 🔵 누리 | NLU 분석가 | 의도·개체·감정 분석 (구조화 출력) |
| 🟣 다인 | 대화 설계자 | 응답 톤/전략/핵심 포인트 설계 |
| 🟢 로운 | 응답 생성가 | 대화 이력 기반 응답 초안 작성 (adaptive thinking) |
| 🩷 세아 | 품질 검수자 | 초안 검수 및 최종 응답 확정 |

각 에이전트의 상태(💤 휴식 / 💭 생각 / ⚙️ 작업 / ✅ 완료 / ❌ 오류)가 SSE로 브라우저에 스트리밍되어, 캐릭터가 휴게실과 책상 사이를 오가며 일하는 모습이 애니메이션됩니다.

## 실행 방법

```bash
pip install -r requirements.txt

# Claude API 연동 (선택 — 없으면 데모 모드로 동작)
export ANTHROPIC_API_KEY=sk-ant-...

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

브라우저에서 http://localhost:8000 접속.

- **LIVE 모드**: `ANTHROPIC_API_KEY`(또는 `ANTHROPIC_AUTH_TOKEN`)가 설정되어 있으면 Claude(`claude-opus-4-8`, `CHATBOT_MODEL` 환경변수로 변경 가능)가 실제로 파이프라인을 수행합니다. 메시지 1건당 LLM 호출이 4회(NLU→설계→생성→검수) 발생하므로 응답에 수십 초가 걸릴 수 있습니다.
- **데모 모드**: API 키가 없으면 정해진 응답과 타이밍으로 파이프라인을 재현합니다. 시각화 확인용으로 그대로 사용할 수 있습니다.

## 구조

```
app/
  main.py     # FastAPI 서버 (채팅 API, SSE 이벤트 스트림, 정적 파일)
  agents.py   # 5-에이전트 파이프라인 (Claude API / 데모 모드)
static/
  index.html  # 레이아웃 (오피스 캔버스 + 채팅 패널)
  office.js   # 픽셀 오피스 렌더러 (캔버스 픽셀아트, 상태 애니메이션)
  app.js      # 채팅 UI + SSE 연결
  style.css
```

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/api/chat` | `{session_id, message}` → 파이프라인 실행 후 `{reply, nlu, design, review}` |
| `GET` | `/api/events` | 에이전트 상태 SSE 스트림 (`{agent, state, activity}`) |
| `GET` | `/api/agents` | 에이전트 명단 + 모드(LIVE/데모) 정보 |
