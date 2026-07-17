# 개념도 패턴 카탈로그 — 실물 역설계 설계 언어

- 작성: 다인 / 발주 배경: "구성도 개념도 너무 못 만든다" — 현행 개념도가 토픽 성격과 무관하게
  "박스 열 → 화살표 → 허브 → 화살표 → 박스 열" 단일 형태로 찍혀 나오던 문제.
- 실물 근거: **MG 취합본 도식 34건 전수 분류**(본문 수록 54개 토픽, 도식 이미지는 OCR에서
  소실되어 캡션·인접 표·표준 도식형으로 복원 판정) + 방법론 도형 규칙([자] 흐름별 도형 사용,
  대분류 긴 사각형+소분류, 6줄 규격 / [1교시 42~47/65] 구성도 6줄·그림 배치).
- 구현: `api/_agents.py` `_ANSWER_TEMPLATE` CSS "개념도 패턴 키트" 블록. 견본: `docs/diagram-samples.html`.
- 계약 원칙: **모든 패턴은 고정 높이 컨테이너(.diagram 6줄 / .diagram.d7 7줄) 내부의 flex/grid
  배치**다. 패턴이 바뀌어도 컨테이너 높이는 불변 → 페이지 줄 계측(_layout)·분량계와 무충돌.

## 1. 실물 출현 빈도 (MG 표본 34건)

| 패턴 | 비율 | 실물 대표 예 |
|---|---|---|
| ② 계층/분류형 | ~24% | SoW·SLA 구성 트리, Decision Tree, DRP 문서 분류, MECE |
| ① 흐름형 | ~22% | Data Mining 수행단계, Opinion Mining 파이프라인, OLAP 흐름, Value Chain |
| ⑤ 허브형 | ~18% | ITSM(중심+4축), 5 Force, PEST, CRM, ERP |
| ⑥ 비교대칭형 | ~15% | DRS(메인센터↔DR센터), VRM(개인↔기업), Inside-out/Outside-in |
| ③ 레이어형 | ~12% | 신경망(입력/은닉/출력층), EA(BA/AA/DA/TA), CPND, BI 스택 |
| ④ 순환형 | ~9% | CRISP-DM 6단계, BCM PDCA, Cyclone Model |
| ⑦ 로드맵형 | 서론 전용 | 기존 d7 (등장배경 3원+3단 구분) |

반복 시각 요소(실물): 단계 아래 **산출물/기법 병기**(CRISP-DM), 중심+방사(5 Force),
좌우 진영+양방향 화살표+연결 기술 라벨(DRS DWDM), 층 스택+레이어명(신경망),
폐루프 순환(PDCA), root→leaf 트리(Decision Tree), **대분류 상단 가로 바**+하단 흐름(Value Chain).

## 2. 패턴별 클래스 설계 (모두 .diagram 직계 자식 1개로 사용)

공통 프리미티브(기존 유지): `d-box`(+`.soft` 점선 보조, +`.d-hub` 중심 강조), `d-box small`(부제),
`d-row`, `d-col`, `d-arrow`, `d-circle`, `d-sep`.

### ① 흐름형 `.d-flow`
단계 좌→우, 균등 폭(flex:1). 산출물·기법 병기는 단계를 `d-col`로 감싸고 아래 `.d-out`(점선 칩).
```html
<div class="diagram"><div class="d-flow">
  <div class="d-col"><div class="d-box">Sampling</div><div class="d-out">표본 DB</div></div>
  <span class="d-arrow">→</span>
  <div class="d-col"><div class="d-box">전처리</div><div class="d-out">정제 데이터</div></div>
  <span class="d-arrow">→</span>
  <div class="d-col"><div class="d-box">Modeling</div><div class="d-out">모델·규칙</div></div>
</div></div>
```
줄 정합: d-flow는 컨테이너 안 수직 중앙, 6줄 고정. 단계 3~5개, 6개 이상은 ④ c6 또는 2단 d-col.

### ② 계층/분류형 `.d-tree`
대분류 **긴 사각형** `.d-bar`([자] "대분류의 경우 긴 사각형 모양 활용") → 수직 스템 `.d-stem`
→ 갈래 가로선 `.d-branch`(⊓, 바깥 두 갈래) → 하위 `d-row`(박스 2~4개).
```html
<div class="diagram"><div class="d-tree">
  <div class="d-bar">SLA<small>서비스 수준 협약</small></div>
  <div class="d-stem"></div><div class="d-branch"></div>
  <div class="d-row"><div class="d-box">기본계약서</div><div class="d-box">SoW</div>
    <div class="d-box">SLO 지표</div><div class="d-box soft">보상체계</div></div>
</div></div>
```

### ③ 레이어형 `.d-stack`
층 스택(위=상위 계층), 전폭 72% 바 나열. 층별 부제는 `small`.
```html
<div class="diagram"><div class="d-stack">
  <div class="d-box">표현 계층<small>BI · 시각화</small></div>
  <div class="d-box">분석 계층<small>OLAP · Mining</small></div>
  <div class="d-box soft">데이터 계층<small>DW · Data Mart</small></div>
</div></div>
```
층 3~4개. 층간 화살표 필요 시 층 사이 `<span class="d-arrow">↓</span>` 삽입(d-stack이 세로 flex).

### ④ 순환형 `.d-cycle` (+`.c6`)
폐루프. 4단계 = 2×2 grid + 좌`↑`/우`↓` 회귀 화살표 `span.d-turn`(2행 스팬).
6단계(CRISP-DM류) = `.d-cycle.c6` (상단 3 + 하단 3).
```html
<div class="diagram"><div class="d-cycle">
  <span class="d-turn">↑</span>
  <div class="d-box">Plan<small>BCP 수립</small></div><span class="d-arrow">→</span>
  <div class="d-box">Do<small>구축·운영</small></div>
  <span class="d-turn">↓</span>
  <div class="d-box">Act<small>유지·개선</small></div><span class="d-arrow">←</span>
  <div class="d-box">Check<small>모의훈련</small></div>
</div></div>
```
DOM 순서 계약(그리드 자동 배치): `d-turn(좌) → 상단행(박스·→ 교대) → d-turn(우) → 하단행(역순, ← 교대)`.

### ⑤ 허브형 `.d-net`
3×3 그리드: 모서리 4요소, 중앙 `d-box d-hub`, 변 중앙에 방향 `d-arrow`(중심 지향).
```html
<div class="diagram"><div class="d-net">
  <div class="d-box">인력<small>People</small></div><span class="d-arrow">↓</span><div class="d-box">조직</div>
  <span class="d-arrow">→</span><div class="d-box d-hub">ITSM</div><span class="d-arrow">←</span>
  <div class="d-box">기술</div><span class="d-arrow">↑</span><div class="d-box">프로세스</div>
</div></div>
```

### ⑥ 비교대칭형 `.d-vs`
좌우 진영 `div.d-zone`(점선 테두리, 첫 줄 `.d-zt` 진영 라벨) + 중앙 `.d-link`(연결 화살표+기술 라벨).
실물 DRS: 좌 메인센터 / 우 DR센터 / 중앙 "DWDM·복제 솔루션".
```html
<div class="diagram"><div class="d-vs">
  <div class="d-zone"><div class="d-zt">메인 센터</div>
    <div class="d-box">Web · WAS</div><div class="d-box">DBMS · Storage</div></div>
  <div class="d-link"><span class="d-arrow">⇄</span>DWDM<small>실시간 복제</small></div>
  <div class="d-zone"><div class="d-zt">재해복구 센터</div>
    <div class="d-box">HA 대기계<small>Stand-by</small></div><div class="d-box soft">동일 구성 유지</div></div>
</div></div>
```

### ⑦ 로드맵형 (서론 전용, 기존)
`.diagram.d7` + `d-circle`×3 + `d-sep` + 중앙 `d-col`(hub+soft 박스) + 결과 박스. 변경 없음.

## 3. 토픽 성격 → 패턴 선택 가이드 (조립기/로운·부품 작성자용)

| 토픽 성격 신호 | 패턴 | 판별 힌트(부품 필드) |
|---|---|---|
| 절차·수행단계·파이프라인 ("단계", "프로세스", 산출물 존재) | ① d-flow | components의 name이 단계명 연쇄 |
| 구성 분해·문서 체계·유형 분류 ("구성", "유형", "체계") | ② d-tree | 상위 1개+하위 병렬 나열 |
| 아키텍처 계층·스택 ("계층", "Layer", "층") | ③ d-stack | 상하 관계 명확 |
| 관리 사이클·개선 루프 (PDCA, 순환, 회귀) | ④ d-cycle | 마지막 단계→첫 단계 회귀 |
| 중심 개념+구성 축·환경 요인 ("중심", 4~5요소 대등) | ⑤ d-net | 요소 간 순서 없음 |
| 이중화·대비·양자 관계 ("vs", 센터 이중화, 상호작용) | ⑥ d-vs | 두 진영+연결 기술 |
| 서론(등장배경→도입→효과) | ⑦ d7 | intro_diagram 슬롯 전용 |

우선순위 규칙: 순환 신호가 있으면 ④가 ①에 우선(CRISP-DM을 흐름으로 그리지 말 것 — 실물은 순환).
분류와 허브가 겹치면 "요소가 중심에 결합하는가(⑤) / 중심에서 분해되는가(②)"로 판정.

## 4. 부품 스키마 호환

- `diagram_html`/`intro_diagram_html`은 종전대로 완성 HTML 문자열 — 신규 클래스만 추가됐고
  기존 d-row/d-col/d-box 부품은 그대로 유효(하위호환). 재작성 배치는 §3 가이드로 패턴 선택.
- 빌드 게이트(`scripts/build_index.py`)의 `<script>`·외부 URL 금지는 그대로 통과(순수 CSS 클래스).
- 신규 클래스 전량: `d-flow, d-out, d-bar, d-tree, d-stem, d-branch, d-stack, d-cycle(+c6),
  d-turn, d-net, d-vs, d-zone, d-zt, d-link` — `_BODY_RULES`(LLM 계약)와 answer-template-spec §7에 반영됨.

## 5. 금지 사항

- 패턴 없이 d-box를 일렬 나열(현행 반려 형태) — 반드시 §3에서 하나 선택.
- 컨테이너 높이 변경(6줄/7줄 외 값) — 페이지 계측 파괴.
- 개념도 내 텍스트 과밀: 박스 라벨 8자 이내 + small 12자 이내, 박스 6개 초과 금지(d-cycle.c6 예외).
- 본문 개념도 뒤 간글 생략(체크리스트 G1 — 린터 강제 예정).
