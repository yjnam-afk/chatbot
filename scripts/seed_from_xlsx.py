#!/usr/bin/env python3
"""경영전략_토픽 CSV → 토픽 뼈대 JSON 생성 (부품 보존 머지).

사용법:
    python3 scripts/seed_from_xlsx.py <csv경로>

입력 CSV: 경영전략_토픽.xlsx의 1번 시트를 CSV로 export한 파일.
컬럼: No / 토픽 이름 / 분류 / 키워드(암기) / 암기법(해당경우) / 정의 요약 (35자 내외)

동작:
- No가 숫자인 행마다 api/_library/topics/MG-<No 3자리>.json 생성/갱신.
- 뼈대 필드(name, category, keywords, definition, mnemonic.word)는 CSV 값으로 갱신.
- aliases는 자동 파생분과 기존(수작업) 값을 합집합 — 수작업 별칭 보존.
- 풀부품 필드(definition_long, diagram_html, components, comparisons, ...)는 절대 건드리지 않음.
- 짧은 이름이 다른(더 앞선 No) 토픽의 짧은 이름과 충돌하면 뒤 토픽에서 해당 별칭 제거
  (예: MG-080 "ITSM (ISO/IEC 20000)"의 별칭 "ITSM"은 MG-001 소유).
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))
from _topic_library import norm  # noqa: E402

TOPICS_DIR = ROOT / "api" / "_library" / "topics"


def short_base(name: str) -> str:
    """괄호를 제거한 짧은 이름."""
    base = re.sub(r"\([^)]*\)", " ", name or "")
    return re.sub(r"\s+", " ", base).strip()


def derive_aliases(name: str) -> list[str]:
    """토픽 이름에서 별칭 자동 파생: 괄호 제거형, 괄호 내부(나열 제외), 슬래시 분할."""
    aliases: list[str] = []
    base = short_base(name)
    if base and base != name:
        aliases.append(base)
    for inner in re.findall(r"\(([^)]*)\)", name or ""):
        inner = inner.strip()
        # 콤마 나열(약어 풀이 목록 등)은 별칭으로 부적합 — 단일 구만 채택
        if inner and "," not in inner and len(inner) <= 60:
            aliases.append(inner)
    for part in re.split(r"[/]", base):
        part = part.strip()
        if part and part != base:
            aliases.append(part)
    out: list[str] = []
    for a in aliases:
        if a and a != name and a not in out:
            out.append(a)
    return out


def split_keywords(raw: str) -> list[str]:
    items = [k.strip() for k in re.split(r"[,\n]", raw or "")]
    return [k for k in items if k][:20]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    csv_path = Path(sys.argv[1])
    rows = list(csv.reader(csv_path.open(encoding="utf-8-sig")))
    data_rows = [r for r in rows if r and r[0].strip().isdigit()]
    if not data_rows:
        print("오류: No가 숫자인 데이터 행이 없습니다.")
        return 1

    TOPICS_DIR.mkdir(parents=True, exist_ok=True)

    # 짧은 이름 → 최소 No (별칭 충돌 해소용)
    owner: dict[str, int] = {}
    for r in data_rows:
        no = int(r[0])
        key = norm(short_base(r[1]))
        if key and (key not in owner or no < owner[key]):
            owner[key] = no

    created, updated = 0, 0
    for r in data_rows:
        r = (r + [""] * 6)[:6]
        no, name, category, kw_raw, mn_raw, definition = (c.strip() for c in r)
        no_i = int(no)
        tid = f"MG-{no_i:03d}"
        keywords = split_keywords(kw_raw)
        auto_aliases = derive_aliases(name)

        path = TOPICS_DIR / f"{tid}.json"
        data = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            updated += 1
        else:
            created += 1

        # 뼈대 필드 갱신 (풀부품 필드는 그대로 보존)
        data["id"] = tid
        data["name"] = name
        data["category"] = category
        data["keywords"] = keywords
        data["definition"] = definition
        mn = data.get("mnemonic") or {}
        mn["word"] = mn_raw or mn.get("word", "")
        if not mn.get("expansion"):
            mn["expansion"] = keywords[:6]
        data["mnemonic"] = mn

        # 별칭: 자동 파생 + 기존(수작업) 합집합, 이후 충돌 해소
        merged = list(data.get("aliases") or [])
        for a in auto_aliases:
            if a not in merged:
                merged.append(a)
        data["aliases"] = [
            a for a in merged
            if not (norm(a) in owner and owner[norm(a)] != no_i and norm(a) != norm(name))
        ]

        # 필드 순서 정리 후 저장
        ordered = {k: data[k] for k in (
            "id", "name", "aliases", "category", "keywords", "mnemonic", "definition")}
        for k, v in data.items():
            if k not in ordered:
                ordered[k] = v
        path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    print(f"완료: 생성 {created}건, 갱신 {updated}건 → {TOPICS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
