"""결정적 fake collector (계획 §6) — vendor 자격증명 없이 lifecycle 을 재현한다.

같은 (seed, scenario, request) 는 실행 시각·실행 순서와 무관하게 항상 같은 record 와
checksum 을 만든다. 값은 난수가 아니라 sha256 유도라 플랫폼 간에도 결정적이다.
digest 입력의 datetime 은 canonical_json 을 거쳐 UTC `Z` 로 정규화된다 — 같은 순간을
KST 로 주든 UTC 로 주든 같은 값이 나온다.

scenario fixture(JSON)가 vendor 의 이상 동작을 선언한다. **미지 키·틀린 타입·범위 밖
값·요청 universe 에 없는 unit 은 즉시 거부한다**(Rule 12) — fixture 오류가 조용히
no-op 시나리오가 되면 실패 경로 테스트가 아무것도 검증하지 않은 채 초록이 된다:
- 가격: 일부 unit 누락(missing) / 무거래(no-trade) / stale timestamp / late correction.
  no-trade 와 missing 은 다르다 — no-trade 는 성공(bar 없음이 사실), missing 은 실패다.
- 뉴스: duplicate NEWS_ID / page drift / anchor miss(burst) / late correction.
  row 형상은 기존 normalize·`parse.bigkinds_date` 가 소비하는 실제 BigKinds 필드
  (NEWS_ID `.<YYYYMMDD><6자리>`·DATE·TITLE·CONTENT·PROVIDER·PROVIDER_LINK_PAGE)를 따른다.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from itertools import combinations

from .models import CollectionRequest, CollectionResult, canonical_json, content_checksum
from .states import WINDOW_INCOMPLETE, WINDOW_VALID


def _digest(*parts: object) -> int:
    blob = canonical_json(list(parts)).encode("utf-8")
    return int.from_bytes(hashlib.sha256(blob).digest()[:8], "big")


def _require_known_keys(scenario: dict, allowed: frozenset[str], where: str) -> None:
    unknown = set(scenario) - allowed
    if unknown:
        raise ValueError(f"{where} scenario 미지 키: {sorted(unknown)} — 오타면 시나리오가 no-op 이 된다")


def _id_set(scenario: dict, key: str, where: str) -> frozenset[str]:
    """unit ID 목록 읽기 — 문자열을 그대로 주면 문자 집합으로 쪼개져 조용히 무력화되므로
    list/tuple of str 만 받는다."""
    value = scenario.get(key, ())
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{where}.{key} 는 문자열 배열이어야 한다: {value!r}")
    if not all(isinstance(item, str) for item in value):
        raise ValueError(f"{where}.{key} 에 비문자열 항목이 있다: {value!r}")
    if len(set(value)) != len(value):
        # frozenset 으로 접으면 복붙 오류(다른 ID 를 의도한 중복)가 조용히 대상 하나로 준다
        raise ValueError(f"{where}.{key} 에 중복 ID 가 있다: {sorted(value)}")
    return frozenset(value)


def _int_value(scenario: dict, key: str, where: str, *, default: int | None = None, minimum: int = 0) -> int:
    value = scenario.get(key, default)
    if value is None:
        raise ValueError(f"{where}.{key} 가 없다")
    if isinstance(value, bool) or not isinstance(value, int):
        # int() 강제는 1.9→1 처럼 fixture 오류를 조용히 정상값으로 바꾼다
        raise ValueError(f"{where}.{key} 는 정수여야 한다: {value!r}")
    if value < minimum:
        raise ValueError(f"{where}.{key} 는 {minimum} 이상이어야 한다: {value}")
    return value


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
        self._missing = _id_set(scenario, "missing_unit_ids", "price")
        self._no_trade = _id_set(scenario, "no_trade_unit_ids", "price")
        self._stale = _id_set(scenario, "stale_unit_ids", "price")
        self._correction_units = _id_set(correction, "unit_ids", "price.correction")
        if "correction" in scenario and not self._correction_units:
            # 빈/대상 없는 correction 블록({} 포함)은 generation/delta 가드를 모두 우회한다
            raise ValueError("correction 블록에 unit_ids 가 비어 있다")
        # 한 unit 이 두 역할이면 분기 순서가 시나리오 의미를 임의로 정한다 — 배타 강제.
        # correction 은 bar 를 만드는 unit 에만 의미가 있으므로 missing/no_trade 와 배타
        # (stale 과의 조합은 유효 — stale bar 의 값 정정).
        exclusive = ("missing", "no_trade", "stale", "correction_units")
        for left, right in combinations(exclusive, 2):
            if {left, right} == {"stale", "correction_units"}:
                continue
            overlap = getattr(self, f"_{left}") & getattr(self, f"_{right}")
            if overlap:
                raise ValueError(f"price {left}/{right} 에 같은 unit: {sorted(overlap)}")
        self._generation = _int_value(scenario, "generation", "price", default=1, minimum=1)
        if self._correction_units and self._generation < 2:
            # generation 1 이면 _bar 가 delta 를 적용하지 않는다 — 선언한 정정이 조용히
            # no-op 이 되면 correction fixture 가 정상 데이터만 만들고도 초록이 된다
            raise ValueError("correction 을 선언했으면 generation 은 2 이상이어야 한다")
        close_delta = correction.get("close_delta", 0)
        if isinstance(close_delta, bool) or not isinstance(close_delta, int):
            raise ValueError(f"price.correction.close_delta 는 정수여야 한다: {close_delta!r}")
        if self._correction_units and close_delta == 0:
            # delta 0 이면 값이 안 바뀐다 — generation 만 올라가는 무의미한 "정정" 차단
            raise ValueError("correction 을 선언했으면 close_delta 는 0 이 아니어야 한다")
        self._close_delta = close_delta

    def _declared_units(self) -> frozenset[str]:
        return self._missing | self._no_trade | self._stale | self._correction_units

    def _bar(self, unit_id: str, request: CollectionRequest) -> dict:
        base = 1_000 + _digest(self._seed, request.dataset, unit_id, request.window_start) % 99_000
        spread = base // 100 + 1
        close = base + (_digest(self._seed, unit_id, "close") % (2 * spread)) - spread
        if self._generation > 1 and unit_id in self._correction_units:
            close += self._close_delta  # late correction: 같은 window, 값만 정정
        low = min(base, close) - spread
        if low <= 0:
            # 정상 유도 경로는 항상 양수다(base>=1000, |편차|<=2*spread) — correction
            # delta 가 만든 비현실 가격을 fake 가 정상인 척 내보내지 않는다
            raise ValueError(f"close_delta 가 비양수 가격을 만들었다: unit={unit_id} low={low}")
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
            "low": low,
            "close": close,
            "volume": _digest(self._seed, unit_id, "vol") % 100_000,
        }

    def collect(
        self, request: CollectionRequest, now
    ) -> tuple[CollectionResult, tuple[dict, ...], dict[str, list[str]]]:
        """수집 실행 → (result, records, unit manifest).

        `now` 는 주입된 시각 — stage_timestamps 에만 쓰이고 checksum 에는 절대 들어가지
        않는다(같은 request 는 언제 실행해도 같은 checksum). manifest 는 unit 별
        received/no_trade/missing 분류다 — 소비자가 "무엇을 재시도할지"를 result 만으로
        복원할 수 있어야 한다.
        """
        ghost_units = self._declared_units() - set(request.unit_ids)
        if ghost_units:
            # 오타 ID 는 알려진 키 안에 숨으면 조용히 no-op 이 된다 — 여기서 잡는다
            raise ValueError(f"scenario 가 요청 universe 에 없는 unit 을 지목: {sorted(ghost_units)}")

        received, no_trade, missing = [], [], []
        records: list[dict] = []
        # unit_id 정렬 순회 — 같은 멤버십을 다른 순서로 요청해도 records·checksum 이 같다
        # (Universe.universe_hash 의 순서 무관 identity 와 같은 축)
        for unit_id in sorted(request.unit_ids):
            if unit_id in self._missing:
                missing.append(unit_id)
            elif unit_id in self._no_trade:
                no_trade.append(unit_id)  # 성공 — 거래 없는 분엔 분봉이 없다
            else:
                received.append(unit_id)
                records.append(self._bar(unit_id, request))

        manifest = {
            "received": received,
            "no_trade": no_trade,
            "missing": missing,
        }
        # checksum 은 **데이터에서만** 유도한다 — generation 이 들어가면 값이 같은
        # 재실행이 다른 checksum 이 돼 "같은 checksum → generation 불변" 판정이 깨진다
        result_checksum = content_checksum(
            [request.dataset, request.window_start, request.window_end, records]
        )
        result = CollectionResult(
            status=WINDOW_VALID if not missing else WINDOW_INCOMPLETE,
            expected_count=len(request.unit_ids),
            succeeded_count=len(received) + len(no_trade),
            failed_count=len(missing),
            retry_count=0,
            artifact_uri=(
                f"memory://minute/{request.dataset}/{request.session_id}/"
                f"{request.window_start.isoformat()}"
            ),
            # unit 분류에 대한 placeholder checksum — 저장 manifest **바이트**의
            # sha256 은 Worker(PR 4)가 artifacts.build_window_manifest 결과로 채운다.
            # 이 값을 S3 manifest 재해시와 대조하면 안 된다.
            manifest_checksum=content_checksum(
                [manifest["received"], manifest["no_trade"], manifest["missing"]]
            ),
            result_checksum=result_checksum,
            watermark_before=None,
            watermark_after=request.window_end,
            generation=self._generation,
            stage_timestamps={"collection_started_at": now, "collection_finished_at": now},
        )
        return result, tuple(records), manifest


# ── 뉴스 ──

_NEWS_SCENARIO_KEYS = frozenset(
    {"scenario", "date_yyyymmdd", "initial_count", "new_per_poll", "bursts", "duplicate", "late_correction"}
)
_DUPLICATE_KEYS = frozenset({"poll_index", "position", "of_index"})
_BURST_KEYS = frozenset({"poll_index", "count"})
_LATE_CORRECTION_KEYS = frozenset({"poll_index", "article_index"})

_MAX_ARTICLE_INDEX = 999_999  # NEWS_ID 시퀀스가 6자리라 넘으면 실형상 위반


class FakeNewsFeed:
    """BigKinds 최신순 page fake. scenario 는 tests/minute/fixtures/news_*.json 참조.

    시각 커서가 없는 소스라(확정 결정) feed 는 "최신 page 부터 훑기"만 제공한다.
    poll 이 진행할수록 새 기사가 앞에 끼어들어 기존 기사의 page 위치가 밀린다(page drift).
    """

    def __init__(self, scenario: dict, seed: int) -> None:
        _require_known_keys(scenario, _NEWS_SCENARIO_KEYS, "news")
        self._seed = seed
        date_value = scenario.get("date_yyyymmdd", "20260731")
        if not isinstance(date_value, str) or len(date_value) != 8 or not date_value.isdigit():
            # strptime 은 zero-padding 을 강제하지 않아 "2026731" 도 파싱된다 — 자릿수 먼저
            raise ValueError(f"news.date_yyyymmdd 는 8자리 숫자여야 한다: {date_value!r}")
        try:
            datetime.strptime(date_value, "%Y%m%d")
        except ValueError as error:
            # 비달력 날짜(20261399)는 parser 는 통과해도 normalize 게이트에서 전멸한다
            raise ValueError(f"news.date_yyyymmdd 가 YYYYMMDD 달력일이 아니다: {date_value!r}") from error
        self._date = date_value
        self._initial = _int_value(scenario, "initial_count", "news", default=0)
        self._new_per_poll = _int_value(scenario, "new_per_poll", "news", default=0)
        self._bursts = {}
        for burst in scenario.get("bursts", ()):
            _require_known_keys(burst, _BURST_KEYS, "news.bursts[]")
            poll_at = _int_value(burst, "poll_index", "news.bursts[]")
            if poll_at in self._bursts:
                # dict 덮어쓰기는 같은 poll 의 두 burst 를 조용히 하나로 줄인다
                raise ValueError(f"news.bursts[] 에 poll_index={poll_at} 가 중복이다")
            self._bursts[poll_at] = _int_value(burst, "count", "news.bursts[]", minimum=1)
        dup = scenario.get("duplicate")
        self._dup = None
        if dup is not None:
            _require_known_keys(dup, _DUPLICATE_KEYS, "news.duplicate")
            dup_poll = _int_value(dup, "poll_index", "news.duplicate")
            of_index = _int_value(dup, "of_index", "news.duplicate")
            if of_index >= self._published_count(dup_poll):
                # 존재하지 않는 기사의 "중복"은 그냥 신규 기사라 dedupe 테스트가 무력화된다
                raise ValueError(
                    f"duplicate.of_index={of_index} 는 poll {dup_poll} 시점 발행분"
                    f"({self._published_count(dup_poll)}건) 밖이다"
                )
            position = _int_value(dup, "position", "news.duplicate")
            if position > self._published_count(dup_poll):
                # 범위 밖 position 을 feed 끝으로 접으면 페이지 예산 안에서 중복이 안
                # 보여 dedupe 테스트가 조용히 무력화된다
                raise ValueError(
                    f"duplicate.position={position} 은 poll {dup_poll} 시점 feed 길이"
                    f"({self._published_count(dup_poll)}) 를 넘는다"
                )
            self._dup = (dup_poll, position, of_index)
        corr = scenario.get("late_correction")
        self._corr = None
        if corr is not None:
            _require_known_keys(corr, _LATE_CORRECTION_KEYS, "news.late_correction")
            corr_poll = _int_value(corr, "poll_index", "news.late_correction", minimum=1)
            article_index = _int_value(corr, "article_index", "news.late_correction")
            if article_index >= self._published_count(corr_poll - 1):
                # 정정 시점이 아니라 **그 이전 poll** 에 원본이 존재해야 원본→수정본
                # lifecycle 이 성립한다 — 첫 등장부터 수정본이면 정정 시나리오가 아니다
                raise ValueError(
                    f"late_correction.article_index={article_index} 는 poll {corr_poll - 1} "
                    f"시점 발행분({self._published_count(corr_poll - 1)}건) 밖이다"
                )
            self._corr = (corr_poll, article_index)

    def _published_count(self, poll_index: int) -> int:
        total = self._initial + poll_index * self._new_per_poll
        total += sum(count for at, count in self._bursts.items() if at <= poll_index)
        return total

    def _article(self, index: int, poll_index: int) -> dict:
        if index > _MAX_ARTICLE_INDEX:
            raise ValueError(f"기사 index {index} 가 NEWS_ID 6자리 시퀀스 상한을 넘는다")
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
        if poll_index < 0:
            raise ValueError("poll_index 는 0 이상이다")
        if page < 1 or page_size < 1:
            raise ValueError("page 는 1-base, page_size 는 양수다")
        total = self._published_count(poll_index)
        newest_first = list(range(total - 1, -1, -1))
        if self._dup and poll_index >= self._dup[0]:
            position, of_index = self._dup[1], self._dup[2]
            newest_first.insert(position, of_index)  # 범위는 __init__ 이 보증한다
        start = (page - 1) * page_size
        return [self._article(i, poll_index) for i in newest_first[start : start + page_size]]
