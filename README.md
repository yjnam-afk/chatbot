# 🍃 픽셀 사무소 (Pixel Office Maker Team)

동물의 숲 컨셉의 픽셀 광장에서 5인 에이전트 팀이 서로 대화하며 **진짜 동작하는 웹앱을 만들어주는** 메이커 팀입니다. "테트리스 만들어줘" 하면 기획→디자인→개발→QA를 거쳐 미리보기/다운로드 가능한 HTML 작업물이 나옵니다. **Vercel에 무료로 배포**할 수 있고, **무료 LLM**(Groq / Gemini)으로 동작합니다.

- 에이전트 팀 구성은 [harness-100](https://github.com/revfactory/harness-100)의 `38-chatbot-builder` 하네스 패턴(대화설계 → NLU → 통합 → 테스트, 5인 팀)을 따랐습니다.
- 시각화는 [Star-Office-UI](https://github.com/ringhyacinth/Star-Office-UI)에서 영감을 받았으며, 라이선스 문제가 없도록 모든 픽셀아트를 캔버스 코드로 직접 렌더링합니다(외부 에셋 없음).

## 에이전트 팀

| 에이전트 | 역할 | 하는 일 |
|---|---|---|
| 🐶 코디 | 팀장 | 작업 분배와 마무리 |
| 🐱 누리 | 기획자 | 의뢰를 요구사항으로 정리, 제작/질문 판별 |
| 🐰 다인 | 디자이너 | 레이아웃·스타일·UX 설계 |
| 🐸 로운 | 개발자 | 단일 HTML 작업물 구현 (일반 질문이면 바로 답변) |
| 🐻 세아 | QA | 코드 검토·테스트 후 승인 |

에이전트들의 상태와 **서로 주고받는 대화**가 채팅 응답 스트림(NDJSON)에 실려 브라우저로 전달되고, 동물 캐릭터들이 캠프파이어와 책상 사이를 오가며 말풍선으로 대화하는 모습이 애니메이션됩니다. 완성된 작업물은 미리보기 모달과 다운로드 버튼으로 제공됩니다.

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
api/_static/
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

## 자율 개발 팀으로 쓰기 (Claude Code)

웹앱의 팀은 1~2분짜리 작업물을 만들지만, 이 레포에는 **몇 시간이고 자율로 개발하는 진짜 팀** 설정도 들어 있습니다 (`.claude/agents/`).

1. [Claude Code](https://claude.ai/code)에서 이 저장소를 엽니다 (또는 로컬에서 `claude` 실행)
2. 이렇게 지시합니다: *"○○ 만들어줘. 완성 기준 충족할 때까지 자율로 진행하고 단계마다 커밋해."*
3. 메인 세션(코디)이 planner(누리) → designer(다인) → developer(로운) → qa(세아) 서브에이전트를 돌리며 스펙 확정 → 구현 → 검증 사이클을 자율 반복합니다.

밤새 시켜두고 아침에 커밋 로그를 확인하는 게 이쪽입니다. 🌙
