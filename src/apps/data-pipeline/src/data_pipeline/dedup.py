"""런 내 중복 제거 (S002 — 중복없이 저장).

dedup 키는 article_id(parse.make_article_id — URL 기반 안정 id).
같은 기사가 여러 심볼 질의에 걸려 와도 한 런에서 한 번만 저장한다.

런 간(run 간) 중복은 여기서 다루지 않는다 — raw 는 run_id 별 append(재현성)이고,
런 간 dedup 은 Step2 의 canonical article_id 병합(멱등)이 흡수한다.
"""

from __future__ import annotations


class Deduper:
    """본 적 있는 dedup 키를 기억하는 런 스코프 상태."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def is_new(self, key: str) -> bool:
        """처음 보는 키면 True 를 돌려주고 기록한다."""
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    @property
    def seen_count(self) -> int:
        return len(self._seen)
