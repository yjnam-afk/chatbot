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

# ---------------------------------------------------------------- 분류 정규화
# xlsx의 category 1단계가 도메인 별칭 파편(MG/경영전략/IT 경영전략/경영전략(MG) 등 16종)이라
# 서랍 폴더가 무의미 — 1단계를 아래 8개 대분류로 통합한다. (category+name 키워드 룰,
# 순서 = 우선순위. 예: CRISP-DM은 "isp" 오매칭 방지를 위해 데이터 그룹을 전략 그룹보다 앞에)
_CATEGORY_GROUPS = [
    ("ITSM · 아웃소싱", (
        "itsm", "itil", "sla", "slm", "sow", "outsourcing", "아웃소싱",
        "escm", "iso20000", "msp", "mro")),
    ("BCP · 재해복구", (
        "bcp", "bcm", "drs", "drp", "bia", "rto", "rpo", "재해",
        "iso22301", "무정전", "uninterruptible")),
    ("데이터 · 인텔리전스", (
        "mining", "마이닝", "crisp", "신경망", "다이내믹스", "datawarehouse",
        "businessintelligence", "olap", "데이터분석", "의사판단")),
    ("거버넌스 · 컴플라이언스", (
        "governance", "거버넌스", "cobit", "valit", "38500", "iso31000",
        "compliance", "ifrs", "규제", "특허", "지식재산", "csr", "iso26000",
        "iso14000", "iso14001", "defacto", "샌드박스")),
    ("프로세스 · 품질 · 성과", (
        "프로세스", "bpm", "bpr", "bam", "bre", "pdca", "sigma", "품질",
        "iso9000", "sem", "vbm", "bsc", "okr", "성과", "투자평가", "경제성",
        "카노", "제약")),
    ("커머스 · 플랫폼 · 핀테크", (
        "commerce", "커머스", "마케팅", "광고", "o2o", "o4o", "핀테크",
        "인터넷전문은행", "오픈뱅킹", "ipo", "ico", "crowd", "펀딩", "플랫폼",
        "marketplace", "소셜", "구독", "긱이코노미", "programmatic", "테크")),
    ("전략 기획 · 분석도구", (
        "전략수립도구", "전략계획", "외부환경", "ismp", "isp", "babok", "swot",
        "5force", "7s", "mece", "liss", "triz", "decisiontree", "cpnd", "stp",
        "pest", "ahp", "bcg", "valuechain", "캐즘", "악마의강", "flywheel",
        "tamsam", "스타트업", "backcasting", "smart", "mvp", "scamper", "캔버스",
        "이슈탐색", "고객여정", "기획", "가치평가", "기술경영")),
]
_FALLBACK_GROUP = "경영 일반 · 디지털"


def normalize_category(raw_category: str, name: str) -> str:
    """도메인 별칭 1단계를 대분류로 교체: '<대분류> > <원본 2단계 이후 경로>'."""
    hay = norm(raw_category) + norm(name)
    parts = [s.strip() for s in (raw_category or "").split(">") if s.strip()]
    rest = parts[1:]
    group = _FALLBACK_GROUP
    for g, keys in _CATEGORY_GROUPS:
        if any(k in hay for k in keys):
            group = g
            break
    return " > ".join([group] + rest) if rest else group


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
        data["category"] = normalize_category(category, name)
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
