#!/usr/bin/env python3
"""토픽 라이브러리 검증 + 역인덱스 생성.

사용법: python3 scripts/build_index.py
- api/_library/topics/*.json 스캔 → 스키마 검증 → api/_library/index.json 재생성.
- 검증 실패 시 exit 1 (커밋 전 반드시 실행 — docs/library-spec.md 2-2).

검증 항목:
- 필수 필드: id, name, aliases, category, keywords, mnemonic, definition
- id == 파일명, "MG-" + 3자리 형식
- HTML 부품(diagram_html, intro_diagram_html) 금칙어: <script, 외부 URL, 인라인 이벤트
- 전체 라이브러리 총량 5MB 이하
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))
from _topic_library import topic_terms  # noqa: E402

LIB_DIR = ROOT / "api" / "_library"
TOPICS_DIR = LIB_DIR / "topics"
INDEX_PATH = LIB_DIR / "index.json"

REQUIRED = ("id", "name", "aliases", "category", "keywords", "mnemonic", "definition")
HTML_FIELDS = ("diagram_html", "intro_diagram_html")
FORBIDDEN = ("<script", "javascript:", "http://", "https://", "onerror=", "onclick=", "onload=", "<iframe")
MAX_TOTAL_BYTES = 5 * 1024 * 1024
# 풀부품 판정: 이 중 2개 이상 보유
FULL_FIELDS = ("definition_long", "diagram_html", "components", "features", "comparisons", "usage")


def fail(msg: str) -> None:
    print(f"검증 실패: {msg}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    files = sorted(TOPICS_DIR.glob("*.json"))
    if not files:
        fail(f"토픽 파일 없음: {TOPICS_DIR}")

    total = sum(f.stat().st_size for f in files)
    if total > MAX_TOTAL_BYTES:
        fail(f"총량 초과: {total / 1024:.0f}KB > 5MB")

    terms: dict[str, list] = {}
    topics_meta: dict[str, dict] = {}
    for f in files:
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            fail(f"{f.name}: JSON 파싱 오류 — {e}")
        for k in REQUIRED:
            if k not in t:
                fail(f"{f.name}: 필수 필드 누락 — {k}")
        if not re.fullmatch(r"MG-\d{3}", t["id"]) or t["id"] != f.stem:
            fail(f"{f.name}: id 형식/파일명 불일치 — {t['id']}")
        if not isinstance(t["aliases"], list) or not isinstance(t["keywords"], list):
            fail(f"{f.name}: aliases/keywords는 배열이어야 함")
        if not isinstance(t["mnemonic"], dict) or "word" not in t["mnemonic"]:
            fail(f"{f.name}: mnemonic은 {{word, expansion}} 객체여야 함")
        for hf in HTML_FIELDS:
            html = t.get(hf) or ""
            low = html.lower()
            for bad in FORBIDDEN:
                if bad in low:
                    fail(f"{f.name}: {hf}에 금칙어 '{bad}' 포함")
        # procedure 부품 (스키마 v1.1, question-spec 2-2): [{step, name, desc}]
        proc = t.get("procedure")
        if proc is not None:
            if not isinstance(proc, list) or not proc:
                fail(f"{f.name}: procedure는 비어있지 않은 배열이어야 함")
            for i, p in enumerate(proc):
                if not isinstance(p, dict) or not isinstance(p.get("step"), int) \
                        or not p.get("name") or "desc" not in p:
                    fail(f"{f.name}: procedure[{i}]는 {{step:int, name, desc}} 객체여야 함")
                if p["step"] != i + 1:
                    fail(f"{f.name}: procedure[{i}].step은 1부터 연속 정수여야 함 — {p['step']}")
                if any("<" in str(p.get(k) or "") for k in ("name", "desc")):
                    fail(f"{f.name}: procedure[{i}]에 HTML 태그 금지")
        # lead 리드문형 제목 (스키마 v1.2, 감사 C1): 4~20자 문자열
        lead = t.get("lead")
        if lead is not None and (not isinstance(lead, str) or not 4 <= len(lead.strip()) <= 20
                                 or "<" in lead):
            fail(f"{f.name}: lead는 4~20자 문자열(HTML 금지)이어야 함 — {lead!r}")
        # 실물 원문 보존 훅 (스키마 v1.3, 발주자 반려 2026-07-21 — 재해석 금지):
        # intro_title/structure_title = 제목 원문, components_headers = 표 열 이름 원문
        for tf in ("intro_title", "structure_title"):
            tv = t.get(tf)
            if tv is not None and (not isinstance(tv, str) or not 4 <= len(tv.strip()) <= 40
                                   or "<" in tv):
                fail(f"{f.name}: {tf}는 4~40자 문자열(HTML 금지)이어야 함 — {tv!r}")
        ch = t.get("components_headers")
        if ch is not None:
            if (not isinstance(ch, list) or len(ch) != 3
                    or not all(isinstance(h, str) and 0 < len(h) <= 6 and "<" not in h
                               for h in ch)):
                fail(f"{f.name}: components_headers는 6자 이하 문자열 3개 배열이어야 함")
            if not t.get("components"):
                fail(f"{f.name}: components_headers는 components가 있어야 유효함")
        # sections 확장 단락 부품 (스키마 v1.2, 감사 C2): [{title, keys, kind, rows}]
        secs = t.get("sections")
        if secs is not None:
            if not isinstance(secs, list) or not secs:
                fail(f"{f.name}: sections는 비어있지 않은 배열이어야 함")
            for i, sc in enumerate(secs):
                if not isinstance(sc, dict) or not sc.get("title") \
                        or not isinstance(sc.get("keys"), list) or not sc["keys"] \
                        or sc.get("kind") not in ("t3", "t2") \
                        or not isinstance(sc.get("rows"), list) or not sc["rows"]:
                    fail(f"{f.name}: sections[{i}]는 {{title, keys[], kind:t3|t2, rows[]}} 필요")
                hs = sc.get("headers")  # 실물 원문 열 이름 (v1.3) — t3=3열, t2=2열
                if hs is not None:
                    want = 2 if sc["kind"] == "t2" else 3
                    if (not isinstance(hs, list) or len(hs) != want
                            or not all(isinstance(h, str) and 0 < len(h) <= 6
                                       and "<" not in h for h in hs)):
                        fail(f"{f.name}: sections[{i}].headers는 6자 이하 문자열 "
                             f"{want}개 배열이어야 함({sc['kind']})")
                need = ("item",) if sc["kind"] == "t2" else ("name",)
                for j, row in enumerate(sc["rows"]):
                    if not isinstance(row, dict) or not all(row.get(k) for k in need):
                        fail(f"{f.name}: sections[{i}].rows[{j}] 필수 키 누락({need})")
                    if any("<" in str(v) for v in row.values() if isinstance(v, str)):
                        fail(f"{f.name}: sections[{i}].rows[{j}]에 HTML 태그 금지")
        # mnemonic.extra (스키마 v1.2): 보조 두문자 줄
        extra = (t.get("mnemonic") or {}).get("extra")
        if extra is not None and (not isinstance(extra, str) or "<" in extra):
            fail(f"{f.name}: mnemonic.extra는 문자열(HTML 금지)이어야 함")
        # comparisons.vs 상호참조 검사(존재하는 id인지)는 전체 로드 후
        tid = t["id"]
        full = sum(1 for k in FULL_FIELDS if t.get(k)) >= 2
        # 풀부품은 2교시 서론 로드맵(Type IV) 부품 필수 — 없으면 발주자가 반려한
        # 텍스트 약식 서론(Type I 유사)으로 조립되므로 게이트에서 차단 (2026-07 3차 반려)
        if full and not t.get("intro_diagram_html"):
            fail(f"{f.name}: 풀부품인데 intro_diagram_html(서론 로드맵 d7) 없음 — Type IV 필수")
        topics_meta[tid] = {
            "name": re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", " ", t["name"])).strip() or t["name"],
            "category": t["category"],
            "mn": (t["mnemonic"].get("word") or ""),
            "def": t["definition"],
            "full": full,
        }
        seen = set()
        for term, w, typ in topic_terms(t):
            key = (term, typ)
            if key in seen:
                continue
            seen.add(key)
            terms.setdefault(term, []).append({"id": tid, "w": w, "t": typ})

    # comparisons.vs 참조 무결성
    for f in files:
        t = json.loads(f.read_text(encoding="utf-8"))
        for cmp_ in t.get("comparisons") or []:
            vs = cmp_.get("vs")
            if vs and vs not in topics_meta:
                fail(f"{f.name}: comparisons.vs가 존재하지 않는 토픽 참조 — {vs}")

    index = {"terms": terms, "topics": topics_meta}
    INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")) + "\n",
                          encoding="utf-8")
    full_n = sum(1 for v in topics_meta.values() if v["full"])
    print(f"OK: 토픽 {len(topics_meta)}건(풀부품 {full_n}) / 용어 {len(terms)}개 / "
          f"총 {total / 1024:.0f}KB → {INDEX_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
