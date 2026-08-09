"""보관 DART raw 전체 또는 접수일 범위를 forward 함수로 재처리한다 (ALPHA-895)."""

from __future__ import annotations

from ..config import DbConfig
from ..lake import Storage
from . import (assemble_disclosure_events, load_disclosure, normalize_disclosure,
               normalize_disclosure_segment)


def run(storage: Storage, run_id: str, *, db: DbConfig,
        from_date: str | None = None, to_date: str | None = None) -> int:
    """raw→canonical×2→typed facts→supply events. 미지정 창은 보관 raw 전체다."""
    stages = (
        lambda: normalize_disclosure.run(
            storage, run_id, None, from_date=from_date, to_date=to_date),
        lambda: normalize_disclosure_segment.run(
            storage, run_id, None, from_date=from_date, to_date=to_date),
        lambda: load_disclosure.run(
            storage, run_id, db=db, from_date=from_date, to_date=to_date),
        lambda: assemble_disclosure_events.run(
            storage, run_id, db=db, from_date=from_date, to_date=to_date),
    )
    for stage in stages:
        code = stage()
        if code != 0:
            return int(code)
    return 0
