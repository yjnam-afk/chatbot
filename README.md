# 🖋️ 기술사 답안 사무소

기술사 시험 문제를 붙여넣으면 **실물 답안지(줄 그리드) HTML**을 만들어주는 AI 서비스입니다.
핵심은 **토픽 라이브러리 우선** 아키텍처 — 발주자의 토픽 정리(경영전략 166건)를 부품 JSON으로
커밋해 두고, 문제가 오면 무LLM 매칭으로 즉시 조립합니다.

- **라이브러리 적중**: LLM 0~2콜, **3초 이내, 키 없이도 실답안** (예: "SLA에 대하여 설명하시오 (25점)")
- **미적중**: 라이브 파이프라인 폴백 — 설계→작성→채점(85점 미만/형식 위반 시 1회 보완+재채점), 최대 5콜
- **일반 질문**: 수험 멘토가 대화 도크에서 답변 (1콜)

답안지는 ITPE(강정배) 방법론 재현: 페이지당 22줄 괘선 그리드, 로마 숫자·가나다 자동 목차,
템플릿 자 기반 표 규격(2:2:6 등), 개념도 6줄, 간글, "끝"·두문자 암기 박스. A4 인쇄/PDF 저장 지원.

## 에이전트 팀 (역할 = 코드 단계와 1:1)

| 에이전트 | 역할 | 실제 담당 | LLM |
|---|---|---|---|
| 코디 | 접수 | 배점·교시형 규칙 판별, 일반 질문 분기 | 0콜 |
| 누리 | 토픽 검색 | 인덱스 매칭(이름·별칭·초성·키워드 점수제), 모호 시 후보 선택 | 0~1콜 |
| 다인 | 답안 편집 | 교시형 템플릿 슬롯에 부품 배치·재단 | 0콜 |
| 로운 | 집필 | 접합부 치환, 미등록 토픽 소단락, 폴백 시 전체 집필 | 0~n콜 |
| 세아 | 검증 | 형식 린트(필수 슬롯·표·암기박스·외부리소스 0건), 분량 점검, 폴백 시 채점 | 0~1콜 |

진행은 좌측 레일의 3단계(🔍 토픽 검색 → 📄 조립 → ✅ 검증)로 표시되고, 토픽 서랍 탭에서
라이브러리 166건을 뒤져 바로 답안지를 만들 수 있습니다.

## 1. 무료 LLM 키 (선택 — 라이브러리 적중은 키 없이 동작)

| 공급자 | 발급처 | 환경변수 | 기본 모델 | 비고 |
|---|---|---|---|---|
| **Google Gemini** (추천) | [aistudio.google.com](https://aistudio.google.com/apikey) | `GEMINI_API_KEY` | `gemini-2.5-flash` | 무료 티어 TPM(분당 토큰)이 커서 답안 파이프라인(호출 다수, 긴 출력)에 여유, 한국어 품질 좋음 |
| **Groq** | [console.groq.com](https://console.groq.com) → API Keys | `GROQ_API_KEY` | `llama-3.3-70b-versatile` | 무료, 매우 빠름. 단 무료 TPM이 작아 답안 생성 중 429(Too Many Requests) 가능 |
| 기타 OpenAI 호환 | — | `LLM_API_KEY` + `LLM_BASE_URL` + `LLM_MODEL` | — | OpenRouter 등 |

둘 다 설정된 경우 우선순위는 custom > **Gemini** > Groq 입니다.
키가 없으면 폴백 경로만 데모 모드로 동작합니다(라이브러리 적중 경로는 실답안).

## 2. Vercel 배포

1. 이 저장소를 GitHub에 두고 [vercel.com](https://vercel.com) → **Add New → Project** → 저장소 Import (설정은 기본값 그대로 Deploy)
2. (선택) Vercel 프로젝트 **Settings → Environment Variables**에 `GEMINI_API_KEY` 추가
3. Redeploy → 발급된 URL 접속

함수 최대 실행시간은 `vercel.json`에서 60초로 설정되어 있습니다(Hobby 플랜 한도 내).

## 3. 로컬 실행

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=...      # 선택 (없어도 라이브러리 적중은 실답안)
uvicorn api.index:app --port 8000
```

브라우저에서 http://localhost:8000 접속.

## 토픽 라이브러리 관리

```bash
python3 scripts/seed_from_xlsx.py scripts/data/mg_topics.csv   # xlsx(CSV export) → 뼈대 166건 (풀부품 보존 머지)
# api/_library/topics/MG-*.json 에 부품 수작업 (스키마: api/_library/schema.md)
python3 scripts/build_index.py                                 # 검증 + index.json 재생성 (커밋 전 필수)
python3 tests/test_topic_library.py                            # 매칭 단위 테스트
```

- 토픽당 1 JSON. 뼈대(id/name/aliases/category/keywords/mnemonic/definition)는 자동, 풀부품
  (정의·개념도·구성요소표·비교표·활용 등)은 수작업. 현재 풀부품: ITSM/SLA/SLM/DRS/SWOT.
- HTML 부품에 `<script>`/외부 URL이 섞이면 빌드가 실패합니다(exit 1).

## 구조

```
api/
  index.py           # FastAPI (Vercel 서버리스 함수 / 로컬 겸용)
  _agents.py         # 파이프라인 + 답안지 줄 그리드 템플릿 + 분량/형식 린트
  _topic_library.py  # 무LLM 매칭 엔진 + 라이브러리 정적 로딩
  _assembler.py      # 부품 → 답안 본문 조립기 (슬롯 배치)
  _library/          # topics/MG-*.json (부품) + index.json (빌드 산출물) + schema.md
api/_static/
  index.html         # 헤더 입력줄 + 레일(진행/서랍) + 무대(답안지) + 대화 도크
  progress.js        # NDJSON 이벤트 → 진행 3단계 매핑
  stage.js           # 답안지 iframe 렌더 · 분량 게이지 · 인쇄/PDF/HTML
  drawer.js          # 토픽 서랍 (분류 폴더 · 두문자 카드)
  app.js             # 스트림 파싱 · 교시형 칩 · 대화 도크
scripts/             # seed_from_xlsx.py · build_index.py · data/mg_topics.csv
tests/               # 매칭 엔진 단위 테스트
docs/                # answer-template-spec.md · library-spec.md · front-ui-spec.md + 샘플/목업
```

서버리스 환경에서는 요청 간 메모리가 공유되지 않으므로, 에이전트 이벤트는 `/api/chat` 응답
스트림에 실어 보내고 대화 이력은 브라우저가 유지합니다. 라이브러리는 읽기 전용 정적 데이터라
콜드스타트 캐시만 사용합니다.

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/api/chat` | `{message, history, kind?}` → NDJSON: `{type:"agent"\|"talk"}` 이벤트들 후 `{type:"reply", library, matched, llm_calls, sheet, artifact?, ...}` |
| `GET` | `/api/agents` | 에이전트 명단(역할 표기의 단일 출처) + 모드(LIVE/데모)·공급자·모델 |
| `GET` | `/api/library` | 라이브러리 통계(topics/terms/full_parts) + 서랍용 목록(items) |

## 자율 개발 팀으로 쓰기 (Claude Code)

이 레포에는 **몇 시간이고 자율로 개발하는 진짜 팀** 설정도 들어 있습니다 (`.claude/agents/`).

1. [Claude Code](https://claude.ai/code)에서 이 저장소를 엽니다 (또는 로컬에서 `claude` 실행)
2. 이렇게 지시합니다: *"○○ 만들어줘. 완성 기준 충족할 때까지 자율로 진행하고 단계마다 커밋해."*
3. 메인 세션(코디)이 planner(누리) → designer(다인) → developer(로운) → qa(세아) 서브에이전트를 돌리며 스펙 확정 → 구현 → 검증 사이클을 자율 반복합니다.

밤새 시켜두고 아침에 커밋 로그를 확인하는 게 이쪽입니다. 🌙
