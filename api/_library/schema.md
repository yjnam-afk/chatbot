# 토픽 JSON 스키마 (api/_library/topics/MG-*.json)

기준: `docs/library-spec.md` 2-1. 토픽 1개 = 파일 1개. 갱신 경로는
`scripts/seed_from_xlsx.py`(뼈대) → 수작업 부품 → `scripts/build_index.py`(검증+인덱스) → 커밋.

## 필수 (뼈대 — xlsx에서 자동 생성)

| 필드 | 형 | 설명 |
|---|---|---|
| `id` | str | `"MG-" + xlsx No 3자리`. 파일명과 일치 |
| `name` | str | xlsx 토픽명 원문 |
| `aliases` | list[str] | 영문 풀네임/한글명/음차. 매칭 전용. 자동 파생 + 수작업 추가(머지 시 보존) |
| `category` | str | xlsx 분류 트리 경로 그대로 |
| `keywords` | list[str] | xlsx 키워드(암기) 분해. 매칭 보조(+1) + 본문 소재 |
| `mnemonic` | {word, expansion[]} | xlsx 암기법. 답안지 하단 두문자 박스 |
| `definition` | str | 35자 정의요약 — 1교시형 서두 |

## 풀부품 (선택 — 수작업. 부재 시 조립기는 슬롯 생략 또는 LLM 1콜 보강)

| 필드 | 형 | 슬롯 |
|---|---|---|
| `definition_long` | str | 2교시형 서두 정의(p.def) |
| `background` | str | 필요성/등장배경(p.def) |
| `intro_diagram_html` | str | 2교시 서론 Type IV 로드맵 그림(`div.diagram.d7`). 부재 시 서론은 Type I(정의+필요성) |
| `diagram_html` | str | 본론 개념도(`div.diagram`, 6줄). 템플릿 계약 클래스만 사용 |
| `diagram_gloss` | str | 개념도 간글 1줄 텍스트("– "로 시작) |
| `features` | list[{item, desc}] | 특징표(t2) |
| `components` | list[{role, name, detail}] | 구성요소 상세표(t3: 구분=role/구성요소=name/설명=detail) |
| `components_gloss` | str | 구성요소표 아래 마무리/간글 텍스트 |
| `comparisons` | list[{vs, vs_name, axes[{axis, a, b}]}] | 인접 토픽 비교표(tcmp). `vs`는 존재하는 토픽 id(빌드 시 검사) |
| `usage` | list[{item, desc}] | 활용/기대효과표(t2) — Ⅳ단락 |
| `conclusion` | str | 결론 1~2줄(p.def) |
| `exam_points` | list[str] | 기출 변형 포인트 — 결론부 소재 |
| `related` | list[str] | 복합 문제 연계 후보 토픽 id |

## 규칙

- HTML 부품(`diagram_html`, `intro_diagram_html`)은 답안지 템플릿 계약 클래스만 사용:
  `div.diagram(.d7)`, `d-row/d-col/d-box(.soft/.d-hub)/d-circle/d-sep/d-arrow`, `<small>`, `<br>`.
- 금칙어(빌드 실패): `<script`, `<iframe`, `javascript:`, 외부 URL(`http://`, `https://`), 인라인 이벤트.
- 전체 라이브러리 총량 5MB 이하 (토픽당 평균 30KB).
- 부품 유무가 조립을 깨면 안 된다 — 없는 슬롯은 생략된다.
- 풀부품 판정(index.json `full` 플래그): definition_long/diagram_html/components/features/comparisons/usage 중 2개 이상.

## index.json (빌드 산출물 — 직접 수정 금지)

```json
{
  "terms": {"정규화어": [{"id": "MG-004", "w": 10, "t": "name|cho|part|kw"}]},
  "topics": {"MG-004": {"name": "SLA", "category": "...", "mn": "기소카 메오메보", "def": "...", "full": true}}
}
```

가중치: name/alias 완전형 10 / 초성열 6 / 부분 토큰 5 / 키워드 1 (매칭 규칙은 `api/_topic_library.py`).
