"""사건 파일에서 사이트가 읽는 공개 데이터를 만든다.

- data/index.json: 홈·목록·검색이 쓰는 요약(사건별 한 줄)
- data/cases/<id>.json: 상세 페이지가 읽는 사건 전체

공개본에는 비공개 버킷 경로(sources[].snapshot)와 내부 표시(locked)를 싣지 않는다.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

_INTERNAL_KEYS = ("locked",)
_SUMMARY_KEYS = ("id", "subject", "instruments", "aliases", "headline", "status", "impact",
                 "cover", "flaggedAt", "resolvedAt", "updatedAt", "origin")


def _dump(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def public_case(case: dict) -> dict:
    """상세 페이지에 싣는 사건. 내부 표시와 비공개 보관 위치를 뺀다."""
    out = {k: v for k, v in case.items() if k not in _INTERNAL_KEYS}
    out["sources"] = [{k: v for k, v in s.items() if k != "snapshot"} for s in case["sources"]]
    return out


def summary(case: dict) -> dict:
    """요약 한 줄. types 는 판정 유형을 처음 나온 순서로 한 번씩 싣는다."""
    out = {k: case[k] for k in _SUMMARY_KEYS if k in case}
    out["types"] = list(dict.fromkeys(b["type"] for b in case["behaviors"]))
    return out


def build(cases: list[dict], now: datetime) -> dict[str, bytes]:
    """공개 데이터 파일 묶음(사이트 기준 상대 경로 → 내용)을 만든다. 같은 입력이면 같은 바이트다."""
    ordered = sorted(cases, key=lambda c: c["id"])
    files = {"index.json": _dump({"generatedAt": now.isoformat(timespec="seconds"),
                                  "cases": [summary(c) for c in ordered]})}
    for case in ordered:
        files[f"cases/{case['id']}.json"] = _dump(public_case(case))
    return files


def write(files: dict[str, bytes], out_dir: Path) -> None:
    """파일 묶음을 폴더에 쓴다 (로컬 미리보기·배포 준비용)."""
    for rel, body in files.items():
        path = Path(out_dir) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
