"""결정적 fake collector (계획 §6) — vendor 자격증명 없이 lifecycle 을 재현한다.

같은 (seed, scenario, request) 는 실행 시각·실행 순서와 무관하게 항상 같은 record 와
checksum 을 만든다. 값은 난수가 아니라 sha256 유도라 플랫폼 간에도 결정적이다.
digest 입력의 datetime 은 canonical_json 을 거쳐 UTC `Z` 로 정규화된다 — 같은 순간을
KST 로 주든 UTC 로 주든 같은 값이 나온다.

scenario fixture(JSON)가 vendor 의 이상 동작을 선언한다. **미지 키는 즉시 거부한다**
(Rule 12) — fixture 키 오타가 조용히 no-op 시나리오가 되면 실패 경로 테스트가
아무것도 검증하지 않은 채 초록이 된다:
- 가격: 일부 unit 누락(missing) / 무거래(no-trade) / stale timestamp / late correction.
  no-trade 와 missing 은 다르다 — no-trade 는 성공(bar 없음이 사실), missing 은 실패다.
- 뉴스: duplicate NEWS_ID / page drift / anchor miss(burst) / late correction.
  row 형상은 기존 normalize·`parse.bigkinds_date` 가 소비하는 실제 BigKinds 필드
  (NEWS_ID `.<YYYYMMDD><6자리>`·DATE·TITLE·CONTENT·PROVIDER·PROVIDER_LINK_PAGE)를 따른다.
"""

from __future__ import annotations

import hashlib

from ..ops.states import DATA_INCOMPLETE, DATA_VALID
from .models import CollectionRequest, CollectionResult, canonical_json, content_checksum


def _digest(*parts: object) -> int:
    blob = canonical_json(list(parts)).encode("utf-8")
    return int.from_bytes(hashlib.sha256(blob).digest()[:8], "big")


def _require_known_keys(scenario: dict, allowed: frozenset[str], where: str) -> None:
    unknown = set(scenario) - allowed
    if unknown:
        raise ValueError(f"{where} scenario 미지 키: {sorted(unknown)} — 오타면 시나리오가 no-op 이 된다")


# ── 가격 ──

_PRICE_SCENARIO_KEYS = frozenset(
    {"scenario", "missing_unit_ids", "no_trade_unit_ids", "stale_unit_ids", "generation", "correction"}
)
_CORRECTION_KEYS = frozenset({"unit_ids", "close_delta"})


class FakePriceCollector:
    """window 단위 1분봉 fake. scenario 는 tests/minute/fixtures/price_*.json 참조."""

    def __init__(self, scenario: dict, seed: int) -> None:
        _require_known_keys(scenario, _PRICE_SCENARIO_KEYS, "price")
        correction = scenario.get("correction", {})
        _require_known_keys(correction, _CORRECTION_KEYS, "price.correction")
        self._seed = seed
        self._missing = frozenset(scenario.get("missing_unit_ids", ()))
        self._no_trade = frozenset(scenario.get("no_trade_unit_ids", ()))
        self._stale = frozenset(scenario.get("stale_unit_ids", ()))
        self._correction_units = frozenset(correction.get("unit_ids", ()))
        self._generation = int(scenario.get("generation", 1))
        self._close_delta = int(correction.get("close_delta", 0))

    def _bar(self, unit_id: str, request: CollectionRequest) -> dict:
        base = 1_000 + _digest(self._seed, request.dataset, unit_id, request.window_start) % 99_000
        spread = base // 100 + 1
        close = base + (_digest(self._seed, unit_id, "close") % (2 * spread)) - spread
        if self._generation > 1 and unit_id in self._correction_units:
            close += self._close_delta  # late correction: 같은 window, 값만 정정
        ts = request.window_start
        if unit_id in self._stale:
            # vendor 가 직전 분 봉을 그대로 다시 준 경우 — QC 가 잡아야 할 입력
            ts = request.window_start.__class__.fromtimestamp(
                request.window_start.timestamp() - 60, tz=request.window_start.tzinfo
            )
        return {
            "unit_id": unit_id,
            "ts": ts,
            "open": base,
            "high": max(base, close) + spread,
            "low": min(base, close) - spread,
            "close": close,
            "volume": _digest(self._seed, unit_id, "vol") % 100_000,
        }

    def collect(self, request: CollectionRequest, now) -> tuple[CollectionResult, tuple[dict, ...]]:
        """수집 실행. `now` 는 주입된 시각 — stage_timestamps 에만 쓰이고 checksum 에는
        절대 들어가지 않는다(같은 request 는 언제 실행해도 같은 checksum)."""
        received, no_trade, missing = [], [], []
        records: list[dict] = []
        for unit_id in request.unit_ids:
            if unit_id in self._missing:
                missing.append(unit_id)
            elif unit_id in self._no_trade:
                no_trade.append(unit_id)  # 성공 — 거래 없는 분엔 분봉이 없다
            else:
                received.append(unit_id)
                records.append(self._bar(unit_id, request))

        manifest = [sorted(received), sorted(no_trade), sorted(missing)]
        result_checksum = content_checksum(
            [request.dataset, request.window_start, request.window_end, self._generation, records]
        )
        result = CollectionResult(
            status=DATA_VALID if not missing else DATA_INCOMPLETE,
            expected_count=len(request.unit_ids),
            succeeded_count=len(received) + len(no_trade),
            failed_count=len(missing),
            retry_count=0,
            artifact_uri=(
                f"memory://minute/{request.dataset}/{request.session_id}/"
                f"{request.window_start.isoformat()}"
            ),
            manifest_checksum=content_checksum(manifest),
            result_checksum=result_checksum,
            watermark_before=None,
            watermark_after=request.window_end,
            generation=self._generation,
            stage_timestamps={"collection_started_at": now, "collection_finished_at": now},
        )
        return result, tuple(records)


# ── 뉴스 ──

_NEWS_SCENARIO_KEYS = frozenset(
    {"scenario", "date_yyyymmdd", "initial_count", "new_per_poll", "bursts", "duplicate", "late_correction"}
)
_DUPLICATE_KEYS = frozenset({"poll_index", "position", "of_index"})
_BURST_KEYS = frozenset({"poll_index", "count"})
_LATE_CORRECTION_KEYS = frozenset({"poll_index", "article_index"})


class FakeNewsFeed:
    """BigKinds 최신순 page fake. scenario 는 tests/minute/fixtures/news_*.json 참조.

    시각 커서가 없는 소스라(확정 결정) feed 는 "최신 page 부터 훑기"만 제공한다.
    poll 이 진행할수록 새 기사가 앞에 끼어들어 기존 기사의 page 위치가 밀린다(page drift).
    """

    def __init__(self, scenario: dict, seed: int) -> None:
        _require_known_keys(scenario, _NEWS_SCENARIO_KEYS, "news")
        for burst in scenario.get("bursts", ()):
            _require_known_keys(burst, _BURST_KEYS, "news.bursts[]")
        self._seed = seed
        self._date = str(scenario.get("date_yyyymmdd", "20260731"))
        self._initial = int(scenario.get("initial_count", 0))
        self._new_per_poll = int(scenario.get("new_per_poll", 0))
        self._bursts = {int(b["poll_index"]): int(b["count"]) for b in scenario.get("bursts", ())}
        dup = scenario.get("duplicate")
        if dup is not None:
            _require_known_keys(dup, _DUPLICATE_KEYS, "news.duplicate")
        self._dup = (
            (int(dup["poll_index"]), int(dup["position"]), int(dup["of_index"])) if dup else None
        )
        corr = scenario.get("late_correction")
        if corr is not None:
            _require_known_keys(corr, _LATE_CORRECTION_KEYS, "news.late_correction")
        self._corr = (int(corr["poll_index"]), int(corr["article_index"])) if corr else None

    def _published_count(self, poll_index: int) -> int:
        total = self._initial + poll_index * self._new_per_poll
        total += sum(count for at, count in self._bursts.items() if at <= poll_index)
        return total

    def _article(self, index: int, poll_index: int) -> dict:
        content_rev = 0
        if self._corr and poll_index >= self._corr[0] and index == self._corr[1]:
            content_rev = 1  # 같은 NEWS_ID·URL, 본문만 수정된 late correction
        body_sig = _digest(self._seed, "content", index, content_rev)
        return {
            # 실제 형식: <provider>.<YYYYMMDD><6자리 시퀀스> — parse.bigkinds_date 가 소비
            "NEWS_ID": f"01100901.{self._date}{index:06d}",
            "DATE": self._date,
            "TITLE": f"fixture 기사 {index}",
            "CONTENT": f"본문 {body_sig:016x}",
            "PROVIDER": "픽스처일보",
            "PROVIDER_LINK_PAGE": f"https://news.example/{index}",
        }

    def fetch_page(self, poll_index: int, page: int, page_size: int) -> list[dict]:
        """poll_index 시점의 최신순 `page`(1-base) 를 돌려준다. 결정적이다."""
        if page < 1 or page_size < 1:
            raise ValueError("page 는 1-base, page_size 는 양수다")
        total = self._published_count(poll_index)
        newest_first = list(range(total - 1, -1, -1))
        if self._dup and poll_index >= self._dup[0]:
            position, of_index = self._dup[1], self._dup[2]
            newest_first.insert(min(position, len(newest_first)), of_index)
        start = (page - 1) * page_size
        return [self._article(i, poll_index) for i in newest_first[start : start + page_size]]
