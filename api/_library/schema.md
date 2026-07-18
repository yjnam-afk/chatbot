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
| `diagram_gloss` | str | 개념도 간글 1줄 텍스트("– "로 시작). **부재 시 조립기가 keywords로 합성**(체크리스트 G1 — 본문 그림 뒤 간글 필수라 침묵 생략 없음) |
| `features` | list[{item, desc}] | 특징표(t2) |
| `components` | list[{role, name, detail}] | 구성요소 상세표(t3: 구분=role/구성요소=name/설명=detail) |
| `components_gloss` | str | 구성요소표 아래 마무리/간글 텍스트 |
| `comparisons` | list[{vs, vs_name, axes[{axis, a, b}]}] | 인접 토픽 비교표(tcmp). `vs`는 존재하는 토픽 id(빌드 시 검사) |
| `procedure` | list[{step, name, desc}] | 절차·단계 표(t3 절차표 — 구분열 "N단계") — v1.1 신설(question-spec 2-2). `step`은 1부터 정수, `name` 5~7자, `desc`는 detail과 동일 밀도 규격. 절차·과정·단계를 묻는 요구의 1순위 부품 |
| `lead` | str | Ⅰ 리드문형 제목 앞부분(4~20자) — v1.2 신설(감사 C1). 조립기가 `"{lead}, {토픽}의 개요"`로 스탬프. 모범답안 원문 리드문 우선 |
| `sections` | list[{title, keys[], kind, rows[]}] | 확장 단락 부품 — v1.2 신설(감사 C2, 모범답안 절차/검토항목/위험·해결 단락). 표준 조립은 `sections[0]`을 Ⅲ에 우선 배치(특징표 대체, 비교표는 유지), 요구 주도 조립은 요구 어구가 `keys`를 지목하면 해당 표 배치. `kind`: `t3`(rows=role/name/detail) 또는 `t2`(rows=item/desc). 밀도 규격은 components와 동일 |
| `comparisons[].a_name` | str | (선택, v1.2) 비교표 기준측 표시명 — ITIL의 SS↔SD처럼 토픽 내부 축 비교용. 부재 시 토픽명 |
| `mnemonic.extra` | str | (선택, v1.2) 두문자 보조 줄 `"보조어 — 풀이"` — 단일 토픽 답안의 암기 박스 둘째 줄(ISP 사실규·필시중 등) |
| `usage` | list[{item, desc}] | 활용/기대효과표(t2) — Ⅳ단락 |
| `conclusion` | str | 결론 1~2줄(p.def) |
| `exam_points` | list[str] | 기출 변형 포인트 — 결론부 소재 |
| `related` | list[str] | 복합 문제 연계 후보 토픽 id |

## 규칙

- **손글씨 밀도 규격** (발주자 직접 규격 2026-07-17 + [실물] 스캔 줄바꿈 실측 —
  글자 수는 **공백 제외, 영문·숫자 반각 0.5자 환산**):
  - 전폭 줄(definition·background·conclusion·gloss): 한 줄 **17~19자**,
    2줄 문단은 **38자 이내**, 간글은 **19자 이내**("– " 포함 20).
  - components.detail: 1줄 행용 문자열 **11자 이하** / 2줄 행(r2)용
    **개조식 list 2항목(각 10자 내외)** 또는 문자열 20자 이내.
    구성요소열(name) **5~7자**, 구분열(role) 3~4자.
  - 조립기(_t3)가 항목 수·길이로 r1/r2를 행 단위 자동 선택하고,
    표 밀도 린터가 **상한 초과를 위반 처리**한다(넘치면 손으로 못 베낀다).
  - **연속 행의 role이 같으면 1열(구분)을 rowspan 병합**(체크리스트 T3) — 카테고리가
    하위 2~3행을 묶는 실물 표 구조는 role 반복으로 표현한다.
  - 셀 약어는 영문 병기(`SLA(Service Level Agreement)`), 소라벨(`장점:`) 활용 가능(T7).
- HTML 부품(`diagram_html`, `intro_diagram_html`)은 답안지 템플릿 계약 클래스만 사용:
  `div.diagram(.d7)`, `d-row/d-col/d-box(.soft/.d-hub)/d-circle/d-sep/d-arrow`, `<small>`, `<br>`
  + 개념도 패턴 키트(`d-flow/d-out/d-bar/d-tree/d-stem/d-branch/d-stack/d-cycle(.c6)/d-turn/`
  `d-net/d-vs/d-zone/d-zt/d-link` — docs/diagram-spec.md §3 가이드로 패턴 선택, 박스 일렬 나열 금지).
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
