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

# 부분 일치(w5) 토큰에서 제외할 범용어 — 오탐 방지
_STOP_TOKENS = {
    "it", "the", "and", "of", "for", "to", "in", "on", "model", "system",
    "서비스", "시스템", "관리", "분석", "기법", "모델", "전략", "기술", "정보",
    "이론", "체계", "방법론", "플랫폼",
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
