"""토픽 라이브러리 — 정규화/용어 추출/정적 로딩 (docs/library-spec.md).

- topics/*.json + index.json 은 레포에 커밋된 읽기 전용 정적 데이터.
- 모듈 전역 캐시는 콜드스타트 캐시일 뿐 요청 간 가변 상태가 아니다 (서버리스 원칙 유지).
- 쓰기 API 없음. 갱신 경로는 scripts/seed_from_xlsx.py → 수작업 부품 → scripts/build_index.py → 커밋.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_LIB_DIR = Path(__file__).resolve().parent / "_library"

# ---------------------------------------------------------------- 정규화

_NORM_RE = re.compile(r"[^0-9a-zㄱ-ㅎ가-힣]")
_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"

# 부분 일치(w5) 토큰·주제어 분리 결과에서 제외할 범용어 — 오탐 방지
# (split_subjects의 미등록 주제 감지에서도 공유 — "성과 관리와 BSC" 오분리 잔여물 차단)
_STOP_TOKENS = {
    "it", "the", "and", "of", "for", "to", "in", "on", "model", "system",
    "서비스", "시스템", "관리", "분석", "기법", "모델", "전략", "기술", "정보",
    "이론", "체계", "방법론", "플랫폼", "성과", "결과", "효과", "방안", "요건",
}


def norm(s: str) -> str:
    """소문자화 + 공백/괄호/특수문자 제거. 한글 음절·자모, 영숫자만 남긴다."""
    return _NORM_RE.sub("", (s or "").lower())


def choseong(s: str) -> str:
    """한글 음절의 초성열 추출 (비한글 문자는 건너뜀). 예: 제로트러스트 → ㅈㄹㅌㄹㅅㅌ"""
    out = []
    for ch in s or "":
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7A3:
            out.append(_CHO[(o - 0xAC00) // 588])
    return "".join(out)


def topic_terms(topic: dict):
    """토픽 1건 → (term, weight, type) 시퀀스.

    - name/alias 완전형: w10 (매칭 시 문제 텍스트 완전 포함이면 +10)
    - name/alias 초성열(4자 이상): w6
    - name/alias 부분 토큰(범용어 제외, 영문 4자+/한글 2자+): w5
    - keyword: w1 (매칭 시 토픽당 상한 +4)
    """
    names = [topic.get("name") or ""] + list(topic.get("aliases") or [])
    for raw in names:
        n = norm(raw)
        if len(n) >= 2:
            yield (n, 10, "name")
        cho = choseong(raw)
        if len(cho) >= 4:
            yield (cho, 6, "cho")
        for tok in re.split(r"[^0-9A-Za-z가-힣]+", raw or ""):
            tn = norm(tok)
            if not tn or tn == n or tn in _STOP_TOKENS:
                continue
            if re.fullmatch(r"[a-z0-9]+", tn):
                if len(tn) >= 4:
                    yield (tn, 5, "part")
            elif len(tn) >= 2:
                yield (tn, 5, "part")
    for kw in topic.get("keywords") or []:
        k = norm(kw)
        if len(k) >= 2:
            yield (k, 1, "kw")


# ---------------------------------------------------------------- 정적 로딩

_INDEX: dict | None = None
_TOPIC_CACHE: dict[str, dict | None] = {}


def load_index() -> dict:
    """index.json 로드 (콜드스타트 1회). 없으면 빈 인덱스 — 기능 저하만, 오류 아님."""
    global _INDEX
    if _INDEX is None:
        p = _LIB_DIR / "index.json"
        try:
            _INDEX = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _INDEX = {"terms": {}, "topics": {}}
    return _INDEX


def load_topic(topic_id: str) -> dict | None:
    """토픽 JSON 로드 (파일당 1회 캐시)."""
    if topic_id not in _TOPIC_CACHE:
        p = _LIB_DIR / "topics" / f"{topic_id}.json"
        try:
            _TOPIC_CACHE[topic_id] = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _TOPIC_CACHE[topic_id] = None
    return _TOPIC_CACHE[topic_id]


def stats() -> dict:
    """GET /api/library 통계."""
    idx = load_index()
    topics = idx.get("topics") or {}
    full = sum(1 for v in topics.values() if v.get("full"))
    return {"topics": len(topics), "terms": len(idx.get("terms") or {}), "full_parts": full}


# ---------------------------------------------------------------- 매칭 엔진 (스펙 2-3, LLM 0콜)

# 복수 토픽 채택 힌트: 비교/관계/나열 표현
_MULTI_RE = re.compile(r"비교|관계|차이|각각|및\s|와\s|과\s|·|,")
_JAMO_RUN_RE = re.compile(r"[ㄱ-ㅎ]{4,}")

HIT_SCORE = 8       # 확정 임계값
CAND_SCORE = 3      # 모호 구간 하한
MAX_MATCH = 3       # 복합 문제 최대 채택 수


def match(question: str) -> dict:
    """문제 텍스트 → 토픽 매칭. 반환:
    {"status": "hit|ambiguous|miss", "matched": [id...], "candidates": [{id,name,definition,score}...]}

    점수: name/alias 완전 포함 +10(긴 term이 짧은 term 흡수) / name 부분 토큰 +5 /
    초성열 일치 +6 / keyword 개당 +1(상한 +4).
    보강: 질문에 자모 초성열(4자+)을 그대로 입력한 경우(예: "ㅅㅂㅅㅅㅈㅎㅇ")는 해당 토픽을
    직접 지칭한 것이므로 완전 포함(+10)급으로 취급 — 초성 별칭 단독 입력도 확정되게 한다.
    확정: 최고점 >= 8. 복수 힌트(비교/관계/나열) 있으면 8점 이상을 최대 3건 채택.
    모호: 3 <= 최고점 < 8, 또는 힌트 없이 상위 2건 동점 → 후보 목록 반환(LLM 1콜 선택용).
    """
    idx = load_index()
    qn = norm(question)
    if not qn:
        return {"status": "miss", "matched": [], "candidates": []}
    jamo_runs = set(_JAMO_RUN_RE.findall(qn))

    name_hits: list[tuple[str, str]] = []  # (term, id) — 완전 포함급
    acc: dict[str, dict] = {}

    def bucket(tid: str) -> dict:
        return acc.setdefault(tid, {"part": 0, "kw": 0, "cho": 0})

    for term, entries in (idx.get("terms") or {}).items():
        if term not in qn:
            continue
        for e in entries:
            tid, typ = e["id"], e["t"]
            if typ == "name":
                name_hits.append((term, tid))
            elif typ == "cho":
                if term in jamo_runs:
                    name_hits.append((term, tid))  # 초성 완전어 입력 → 확정급
                else:
                    bucket(tid)["cho"] = 6
            elif typ == "part":
                bucket(tid)["part"] = 5
            else:  # kw
                b = bucket(tid)
                b["kw"] = min(4, b["kw"] + 1)

    # 흡수 규칙: 다른 토픽의 더 긴 완전 포함 term에 포함되는 짧은 term은 버린다
    surviving: set[str] = set()
    for term, tid in name_hits:
        absorbed = any(term != t2 and term in t2 and tid != tid2
                       for t2, tid2 in name_hits)
        if not absorbed:
            surviving.add(tid)

    scores: dict[str, int] = {}
    for tid in set(list(acc) + [t for _, t in name_hits]):
        b = acc.get(tid) or {"part": 0, "kw": 0, "cho": 0}
        base = 10 if tid in surviving else b["part"]
        scores[tid] = base + b["kw"] + b["cho"]

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    topics_meta = idx.get("topics") or {}
    candidates = [
        {"id": tid, "name": (topics_meta.get(tid) or {}).get("name", tid),
         "definition": (topics_meta.get(tid) or {}).get("def", ""), "score": s}
        for tid, s in ranked[:5] if s >= CAND_SCORE
    ]
    hits = [tid for tid, s in ranked if s >= HIT_SCORE]
    if hits:
        if _MULTI_RE.search(question):
            return {"status": "hit", "matched": hits[:MAX_MATCH], "candidates": candidates}
        if len(hits) >= 2 and scores[hits[0]] == scores[hits[1]]:
            return {"status": "ambiguous", "matched": [], "candidates": candidates}
        return {"status": "hit", "matched": [hits[0]], "candidates": candidates}
    if candidates:
        return {"status": "ambiguous", "matched": [], "candidates": candidates}
    return {"status": "miss", "matched": [], "candidates": []}


def pick_from_candidates(ids: list, candidates: list[dict]) -> list[str]:
    """LLM이 고른 id를 후보 목록 안으로 제한한다 (환각 차단). 최대 MAX_MATCH건."""
    allowed = {c["id"] for c in candidates}
    out = []
    for i in ids or []:
        i = str(i).strip()
        if i in allowed and i not in out:
            out.append(i)
    return out[:MAX_MATCH]
