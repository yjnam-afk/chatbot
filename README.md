# 🏢 픽셀 오피스 챗봇 (Pixel Office Chatbot)

멀티 에이전트 팀이 협업해서 답변을 만들고, 그 과정을 **픽셀아트 오피스**로 실시간 시각화하는 챗봇입니다. **Vercel에 무료로 배포**할 수 있고, **무료 LLM**(Groq / Gemini)으로 동작합니다.

- 에이전트 팀 구성은 [harness-100](https://github.com/revfactory/harness-100)의 `38-chatbot-builder` 하네스 패턴(대화설계 → NLU → 통합 → 테스트, 5인 팀)을 따랐습니다.
- 시각화는 [Star-Office-UI](https://github.com/ringhyacinth/Star-Office-UI)에서 영감을 받았으며, 라이선스 문제가 없도록 모든 픽셀아트를 캔버스 코드로 직접 렌더링합니다(외부 에셋 없음).

## 에이전트 팀

| 에이전트 | 역할 | 하는 일 |
|---|---|---|
| 🟡 코디 | 오케스트레이터 | 턴 시작/종료, 작업 분배 |
| 🔵 누리 | NLU 분석가 | 의도·개체·감정 분석 (JSON 출력) |
| 🟣 다인 | 대화 설계자 | 응답 톤/전략/핵심 포인트 설계 |
| 🟢 로운 | 응답 생성가 | 대화 이력 기반 응답 초안 작성 |
| 🩷 세아 | 품질 검수자 | 초안 검수 및 최종 응답 확정 |

각 에이전트의 상태(💤 휴식 / 💭 생각 / ⚙️ 작업 / ✅ 완료 / ❌ 오류)가 채팅 응답 스트림(NDJSON)에 실려 브라우저로 전달되고, 캐릭터가 휴게실과 책상 사이를 오가며 일하는 모습이 애니메이션됩니다.

## 1. 무료 LLM 키 발급 (하나만 있으면 됩니다)

| 공급자 | 발급처 | 환경변수 | 기본 모델 | 비고 |
|---|---|---|---|---|
| **Groq** (추천) | [console.groq.com](https://console.groq.com) → API Keys | `GROQ_API_KEY` | `llama-3.3-70b-versatile` | 무료, 카드 등록 불필요, 매우 빠름 |
| **Google Gemini** | [aistudio.google.com](https://aistudio.google.com/apikey) | `GEMINI_API_KEY` | `gemini-2.5-flash` | 무료 티어, 한국어 품질 좋음 |
| 기타 OpenAI 호환 | — | `LLM_API_KEY` + `LLM_BASE_URL` + `LLM_MODEL` | — | OpenRouter 등 |

키를 하나도 설정하지 않으면 **데모 모드**로 동작합니다(LLM 호출 없이 파이프라인·시각화만 재현).
모델을 바꾸려면 `LLM_MODEL` 환경변수를 함께 설정하세요.

## 2. Vercel 배포

1. 이 저장소를 GitHub에 두고 [vercel.com](https://vercel.com) → **Add New → Project** → 저장소 Import (설정은 기본값 그대로 Deploy)
2. Vercel 프로젝트 **Settings → Environment Variables**에 `GROQ_API_KEY`(또는 `GEMINI_API_KEY`) 추가
3. Redeploy → 발급된 URL 접속

또는 CLI로:

```bash
npx vercel                    # 프로젝트 연결 + 배포
npx vercel env add GROQ_API_KEY
npx vercel --prod
```

메시지 1건당 LLM 호출이 4회(NLU→설계→생성→검수)입니다. 함수 최대 실행시간은 `vercel.json`에서 60초로 설정되어 있습니다(Hobby 플랜 한도 내).

## 3. 로컬 실행

```bash
pip install -r requirements.txt
export GROQ_API_KEY=gsk_...   # 선택 (없으면 데모 모드)
uvicorn api.index:app --port 8000
```

브라우저에서 http://localhost:8000 접속.

## 구조

```
api/
  index.py    # FastAPI 서버 (Vercel 서버리스 함수 / 로컬 겸용)
  _agents.py  # 5-에이전트 파이프라인 (OpenAI 호환 API, async generator)
public/
  index.html  # 레이아웃 (오피스 캔버스 + 채팅 패널) — Vercel CDN이 직접 서빙
  office.js   # 픽셀 오피스 렌더러 (캔버스 픽셀아트, 상태 애니메이션)
  app.js      # 채팅 UI + NDJSON 스트림 파싱 (대화 이력은 클라이언트가 유지)
  style.css
vercel.json   # /api/* 라우팅 + 함수 실행시간 설정
```

서버리스 환경에서는 요청 간 메모리가 공유되지 않으므로, 에이전트 상태 이벤트는 별도 SSE 채널 대신 `/api/chat` 응답 스트림에 실어 보내고, 대화 이력은 브라우저가 유지해 매 요청에 함께 전송합니다.

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/api/chat` | `{message, history}` → NDJSON 스트림: `{type:"agent",...}` 이벤트들 후 `{type:"reply",...}` |
| `GET` | `/api/agents` | 에이전트 명단 + 모드(LIVE/데모)·공급자·모델 정보 |
