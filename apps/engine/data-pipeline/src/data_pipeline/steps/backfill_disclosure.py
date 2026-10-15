"""보관 DART raw 전체 또는 접수일 범위를 forward 함수로 재처리한다 (ALPHA-895)."""

from __future__ import annotations

import logging
from datetime import date

from ..config import DbConfig
from ..lake import Storage
from . import (assemble_disclosure_events, load_disclosure, normalize_disclosure,
               normalize_disclosure_segment)

logger = logging.getLogger(__name__)


def run(storage: Storage, run_id: str, *, db: DbConfig,
        from_date: str | None = None, to_date: str | None = None) -> int:
    """raw→canonical×2→typed facts→supply events. 미지정 창은 보관 raw 전체다."""
    parsed_from = date.fromisoformat(from_date) if from_date is not None else None
    parsed_to = date.fromisoformat(to_date) if to_date is not None else None
    if parsed_from is not None and parsed_to is not None and parsed_from > parsed_to:
        raise ValueError("from_date must be on or before to_date")

    normalizers = (
        ("supply", lambda: normalize_disclosure.run(
            storage, run_id, None, from_date=from_date, to_date=to_date)),
        ("segment", lambda: normalize_disclosure_segment.run(
            storage, run_id, None, from_date=from_date, to_date=to_date)),
    )
    normalize_results = []
    for producer, stage in normalizers:
        try:
            normalize_results.append(stage())
        except Exception:
            # dual producer는 계보가 독립이다. 한쪽 인프라 예외가 다른 manifest 초기화·기록을
            # 막지 않되, 백필 전체는 hard failure로 닫는다.
            logger.exception("공시 백필 정제 예외(producer=%s)", producer)
            normalize_results.append(1)
    if any(code not in (0, 2) for code in normalize_results):
        return 1

    stages = (
        lambda: load_disclosure.run(
            storage, run_id, db=db, input_run_id=run_id,
            from_date=from_date, to_date=to_date),
        lambda: assemble_disclosure_events.run(
            storage, run_id, db=db, from_date=from_date, to_date=to_date),
    )
    for stage in stages:
        code = stage()
        if code != 0:
            return int(code)
    return 2 if 2 in normalize_results else 0
