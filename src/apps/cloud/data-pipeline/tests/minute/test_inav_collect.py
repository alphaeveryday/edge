"""iNAV window collector 테스트 (ALPHA-851).

의도: 이 모듈이 조용히 틀릴 수 있는 축은 **값이 없어지는 쪽이 아니라 값이 바뀌는 쪽**이다.
1콜이 30분치라 window 라벨 대조가 틀어지면 전 window 가 같은 값이거나 한 칸씩 밀린 채
정상으로 커밋되고, iNAV 는 소급 조회가 불가라 그 오염이 영구적이다.

픽스처에 분해능을 준다 — ETF 2종 × 행 3종. 한 종목·한 행이면 "첫 행을 집는" 구현과
"라벨을 맞춰 집는" 구현이 같은 결과를 내 아무것도 보증하지 못한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from data_pipeline.config import KisNavSource as KisNavSourceConfig
from data_pipeline.minute.inav_collect import (
    KisInavCollector,
    collect_inav_units,
)
from data_pipeline.minute.models import KST, CollectionRequest
from data_pipeline.minute.price_collect import Outcome
from data_pipeline.sources.http import StopFetch
from data_pipeline.sources.kis_inav import KisInavSource
from data_pipeline.sources.kis_nav import KisNavShapeError

NOW = datetime(2026, 8, 10, 9, 31, 5, tzinfo=KST)


def row(stamp: str, nav: str, price: str, dprt: str) -> dict:
    """라이브 실측 행(2026-07-25, 069500)의 필드명·문자열 타입을 그대로 쓴다."""
    return {"bsop_hour": stamp, "nav": nav, "stck_prpr": price, "dprt": dprt,
            "nav_vrss_prpr": "121.24", "acml_vol": "15610871"}


# ETF 2종 × 행 3종. 값이 전부 달라서 **어느 행을 집었는지**가 결과에 남는다.
ROWS = {
    "069500": [row("093000", "100.0", "101", "1.00"),
               row("093100", "200.0", "202", "1.00"),
               row("093200", "300.0", "303", "1.00")],
    "091160": [row("093000", "400.0", "404", "1.00"),
               row("093100", "500.0", "505", "1.00"),
               row("093200", "600.0", "606", "1.00")],
}


def request_for(minute: int, unit_ids=("069500", "091160")) -> CollectionRequest:
    start = datetime(2026, 8, 10, 9, minute, tzinfo=KST)
    return CollectionRequest(
        dataset="etf_inav_minute", window_start=start, window_end=start + timedelta(minutes=1),
        run_id="r1", session_id="s1", execution_mode="resident",
        universe_version="v1", unit_ids=unit_ids,
    )


def collect(request, rows=None, now=NOW, clock=None):
    table = ROWS if rows is None else rows
    return collect_inav_units(
        request, now,
        rows_for=lambda unit_id: table.get(unit_id, []),
        retry_count=lambda: 0,
        clock=clock or (lambda: now),
        artifact_uri="pending://artifact",
    )


class TestWindowSelection:
    def test_그_window_라벨의_행만_고른다(self):
        """1콜 30행 중 window 하나만 남는다. 첫 행을 집는 구현이면 전 window 가 같은
        값이 되고, 한 칸 밀리면 전 구간이 밀린 채 정상으로 커밋된다."""
        result, records, manifest = collect(request_for(31))

        assert [r["nav"] for r in records] == ["200.0", "500.0"]
        assert manifest["received"] == ["069500", "091160"]
        assert result.status == "VALID"

        # 다른 window 는 **다른 값**이어야 한다 — 같으면 라벨 대조가 죽은 것이다
        _, later, _ = collect(request_for(32))
        assert [r["nav"] for r in later] == ["300.0", "600.0"]

    def test_그_window_라벨이_없으면_missing(self):
        """응답은 왔는데 그 분이 아직 안 온 상태 — 재시도로 풀리는 축이다."""
        result, records, manifest = collect(request_for(35))

        assert records == () and manifest["missing"] == ["069500", "091160"]
        assert result.status == "INCOMPLETE"  # VALID_EMPTY 로 접으면 결손이 정상이 된다

    def test_같은_라벨_두_행은_invalid(self):
        """어느 쪽이 참인지 고를 수 없다 — 첫 건을 조용히 채택하면 벤더의 순서 변경만으로
        값과 세대가 흔들린다. invalid 는 재시도로 안 풀리는 축이라 missing 과 가른다."""
        dup = {"069500": [row("093100", "200.0", "202", "1.00"),
                          row("093100", "999.0", "999", "1.00")]}
        result, records, manifest = collect(request_for(31, ("069500",)), rows=dup)

        assert records == () and manifest["invalid"] == ["069500"]
        assert result.status == "INVALID"

    def test_no_trade_칸은_늘_빈다(self):
        """NAV 는 스냅샷이라 '거래 없는 분' 축이 없다. 그래도 4분류 어휘는 지킨다 —
        칸을 빼면 `build_window_manifest` 의 완전분할 검증과 갈린다."""
        _, _, manifest = collect(request_for(31))

        assert manifest["no_trade"] == []
        assert set(manifest) == {"received", "no_trade", "missing", "invalid"}


class TestRecordShape:
    def test_nav_결측은_invalid_괴리_결측은_살린다(self):
        """비대칭이 의도다. NAV 가 없으면 담을 게 없지만, 괴리·현재가가 없다고 행을 버리면
        **소급이 불가한 그 분의 NAV 가 영구히 사라진다**."""
        rows = {
            "069500": [{"bsop_hour": "093100", "nav": "200.0"}],           # 괴리·현재가 결측
            "091160": [{"bsop_hour": "093100", "stck_prpr": "505"}],       # nav 결측
        }
        _, records, manifest = collect(request_for(31), rows=rows)

        assert manifest["received"] == ["069500"] and manifest["invalid"] == ["091160"]
        [record] = records
        assert record["nav"] == "200.0"
        assert record["market_price"] is None and record["premium_pct"] is None

    def test_괴리는_단위를_이름에_담고_벤더값을_그대로_싣는다(self):
        """`dprt` 는 퍼센트인데 `sql_surface.v_nav.premium` 은 비율이다 — 같은 이름을
        쓰면 조인하는 쪽이 100배 틀린 값을 읽는다. 값 자체는 변환하지 않는다."""
        _, records, _ = collect(request_for(31, ("069500",)))

        [record] = records
        assert record["premium_pct"] == "1.00"
        assert "premium" not in record and "dprt" not in record

    def test_fetched_at_이_없어_재실행_checksum_이_같다(self):
        """checksum 은 곧 세대 identity 다 — 실행 시각이 섞이면 값이 같은 재실행마다
        checksum 이 달라져 `ArtifactImmutabilityError` 로 그 window 가 막힌다."""
        later = NOW + timedelta(minutes=7)
        first, records_a, _ = collect(request_for(31))
        second, records_b, _ = collect(request_for(31), now=later, clock=lambda: later)

        assert first.result_checksum == second.result_checksum
        assert records_a == records_b
        assert not any("fetched_at" in record for record in records_a)


class FakeAuth:
    def token(self):
        return "TOKEN"


class FakeClient:
    def request(self, method, url, *, headers=None, data=None, decode=True):
        raise AssertionError("이 테스트는 _fetch_etf 를 대체하므로 HTTP 를 타지 않는다")

    def _sleep(self, seconds):
        pass


def make_collector(etf_map=None, interval_sec=60, fetch=None):
    source = KisInavSource(
        KisNavSourceConfig(app_key="k", app_secret="s"),
        etf_map if etf_map is not None else {"069500": "KR7069500007"},
        FakeClient(),
        interval_sec=interval_sec,
    )
    source.auth = FakeAuth()
    if fetch is not None:
        source._fetch_etf = fetch
    return KisInavCollector(source, clock=lambda: NOW)


class TestKisBinding:
    def test_1분_격자가_아닌_간격은_기동에서_거부한다(self):
        """다른 간격이면 라벨이 1분 격자에 안 맞아 전 unit 이 매 window missing 이 되는데,
        원장에는 '벤더가 안 준다'로 보여 원인이 설정임을 가린다."""
        with pytest.raises(SystemExit, match="interval_sec=60"):
            make_collector(interval_sec=180)

    def test_etf_map_밖_unit_은_missing_이_아니라_invalid(self):
        """universe 와 수집 유니버스가 갈린 것은 **재시도로 안 풀린다**. missing 으로
        접으면 벤더가 안 준 것처럼 보여 매 window 헛되이 재시도된다."""
        collector = make_collector(fetch=lambda *a, **k: ROWS["069500"])
        request = request_for(31, ("069500", "091160"))  # 091160 은 etf_map 에 없다

        result, records, manifest = collector.collect(request, NOW)

        assert manifest["invalid"] == ["091160"] and manifest["received"] == ["069500"]
        assert result.status == "INVALID"

    def test_소스_전역_실패는_전파하고_종목_실패는_격리한다(self):
        """자격증명 하나가 틀렸을 때 전 종목 missing 인 INCOMPLETE 가 매분 쌓이면 아무도
        그 하나를 고치러 가지 않는다(Rule 12)."""
        etf_map = {"069500": "KR7069500007", "091160": "KR7091160002"}

        def unit_error(our_etf_id, symbol, d1, d2, token):
            if our_etf_id == "091160":
                raise ValueError("KIS rt_cd=1 msg_cd=... — 이 종목만")
            return ROWS["069500"]

        collector = make_collector(etf_map=etf_map, fetch=unit_error)
        _, records, manifest = collector.collect(request_for(31), NOW)
        assert manifest["received"] == ["069500"] and manifest["missing"] == ["091160"]

        def source_error(*args, **kwargs):
            raise StopFetch("403 — 앱키 권한")

        blocked = make_collector(etf_map=etf_map, fetch=source_error)
        with pytest.raises(StopFetch):
            blocked.collect(request_for(31), NOW)

    def test_토큰은_종목마다_발급하지_않는다(self):
        """KIS 는 토큰 발급을 **분당 1회**로 막는다 — 종목마다 부르면 첫 window 부터 막힌다."""
        etf_map = {"069500": "KR7069500007", "091160": "KR7091160002"}
        issued = []

        class CountingAuth:
            def token(self):
                issued.append(1)
                return "TOKEN"

        collector = make_collector(
            etf_map=etf_map, fetch=lambda our_id, *a, **k: ROWS[our_id]
        )
        collector.source.auth = CountingAuth()
        collector.collect(request_for(31), NOW)
        collector.collect(request_for(32), NOW)

        assert len(issued) == 1  # 2 window × 2 종목인데 발급은 한 번


class TestVendorBlankAndDrift:
    """KIS 는 **키를 빼는 게 아니라 빈 문자열을 보낸다** — 위 결측 테스트가 키 부재만
    써서, 빈 문자열 분기를 지워도(`if raw is None:`) 전 스위트가 통과했다."""

    def test_빈_문자열_괴리는_행을_죽이지_않는다(self):
        """`_row_defect` 는 `bsop_hour`·`nav` 만 요구하므로 이 행은 수집까지 온다.
        빈 문자열 분기가 없으면 `to_decimal("")` 이 터져 그 unit 이 invalid 가 되고,
        **소급이 불가한 그 분의 NAV 가 영구히 사라진다** — 도크스트링이 막으려는 그 축이다."""
        rows = {"069500": [{"bsop_hour": "093100", "nav": "200.0",
                            "stck_prpr": "", "dprt": ""}]}
        result, records, manifest = collect(request_for(31, ("069500",)), rows=rows)

        assert manifest["received"] == ["069500"] and result.status == "VALID"
        [record] = records
        assert record["nav"] == "200.0"
        assert record["market_price"] is None and record["premium_pct"] is None

    def test_빈_문자열_nav_는_행을_살릴_수_없다(self):
        """비대칭의 반대편 — nav 가 빈 문자열이면 담을 값이 없다. 위 테스트만 있으면
        `_REQUIRED_VALUE` 판정을 지워도 통과한다(둘 다 None 으로 살아버린다)."""
        rows = {"069500": [{"bsop_hour": "093100", "nav": "", "stck_prpr": "505"}]}
        _, records, manifest = collect(request_for(31, ("069500",)), rows=rows)

        assert records == () and manifest["invalid"] == ["069500"]

    def test_응답_형상_위반은_missing_이_아니라_invalid(self):
        """벤더가 `output` 키 이름을 바꾸면(자기 엔드포인트끼리도 `output`/`output2` 로
        갈린다) 재시도로 안 풀린다. missing 으로 접으면 전 unit 이 매 window "벤더가 안
        줬다"로 기록되고, 우리 파서가 깨진 사실은 원장 어디에도 안 남는다."""
        def shape_drift(*args, **kwargs):
            raise KisNavShapeError("KIS rt_cd=0 인데 output 이상: NoneType")

        collector = make_collector(fetch=shape_drift)
        result, records, manifest = collector.collect(request_for(31, ("069500",)), NOW)

        assert manifest["invalid"] == ["069500"] and manifest["missing"] == []
        assert result.status == "INVALID"  # INCOMPLETE(재시도 축)와 갈린다
