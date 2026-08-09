"""iNAV window collector 테스트 (ALPHA-851).

의도: 이 모듈이 조용히 틀릴 수 있는 축은 **값이 없어지는 쪽이 아니라 값이 바뀌는 쪽**이다.
1콜이 30분치라 window 라벨 대조가 틀어지면 전 window 가 같은 값이거나 한 칸씩 밀린 채
정상으로 커밋되고, iNAV 는 소급 조회가 불가라 그 오염이 영구적이다.

픽스처에 분해능을 준다 — ETF 2종 × 행 3종. 한 종목·한 행이면 "첫 행을 집는" 구현과
"라벨을 맞춰 집는" 구현이 같은 결과를 내 아무것도 보증하지 못한다.
"""

from __future__ import annotations

import json
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

    def test_값이_바뀌면_checksum_도_바뀐다(self):
        """**정정이 성립하는 근거다.** 원장은 `checksum` 과 `manifest_checksum` 이 둘 다
        같을 때만 generation 을 유지한다(`_record_window_outcome_tx`). NAV 값만 정정된
        재수집은 4분류가 그대로라 manifest 가 안 바뀌므로, checksum 이 값에 안 걸리면
        generation 이 1 에 머물고 정정본이 같은 키를 겨눠 **조용히 no-op 이거나
        ArtifactImmutabilityError** 가 된다. 위 '재실행은 같다' 단언만으로는 값과 무관한
        checksum 도 통과한다 — 두 단언이 짝이어야 축이 고정된다."""
        corrected = {"069500": [row("093100", "999.0", "202", "1.00")]}
        first, _, manifest_a = collect(request_for(31, ("069500",)))
        second, _, manifest_b = collect(request_for(31, ("069500",)), rows=corrected)

        assert manifest_a == manifest_b            # 4분류는 그대로인데
        assert first.result_checksum != second.result_checksum  # checksum 은 갈린다

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
    """`body` 가 주어지면 그 본문을 돌려준다 — 봉투 처리를 `_fetch_etf` 부터 끝까지 태운다.
    없으면 HTTP 를 타면 안 되는 테스트(=`_fetch_etf` 를 대체한 쪽)라 즉시 터뜨린다."""

    def __init__(self, body=None):
        self.body = body

    def request(self, method, url, *, headers=None, data=None, decode=True):
        if self.body is None:
            raise AssertionError("이 테스트는 _fetch_etf 를 대체하므로 HTTP 를 타지 않는다")
        return json.dumps(self.body)

    def _sleep(self, seconds):
        pass


def make_collector(etf_map=None, interval_sec=60, fetch=None, body=None):
    source = KisInavSource(
        KisNavSourceConfig(app_key="k", app_secret="s"),
        etf_map if etf_map is not None else {"069500": "KR7069500007"},
        FakeClient(body),
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

    @pytest.mark.parametrize("body", [
        {"rt_cd": "0", "output": {"nav": "1"}},   # list 가 아니다
        {"rt_cd": "0", "output": None},           # null
        {"rt_cd": "0"},                           # 키 자체가 없다
        {"rt_cd": "0", "output": []},             # 비었다
        [1, 2, 3],                                # 본문이 객체조차 아니다
    ])
    def test_봉투_형상_위반은_전부_재시도_축이다(self, body):
        """봉투 수준은 **한 축이다.** 이 레포의 선은 봉투(전송 사고)↔행·값(INVALID)이고
        (`kis_minute` 이 같은 조건들을 묶어 `KisUnitError` 로 돌린다), 봉투를 INVALID 로
        올리면 안 되는 이유는 **블라스트 반경**이다: `status_of` 는 invalid 하나로 window
        전체를 INVALID 로 만들고 INVALID 는 재청구 대상이 아닌데, iNAV 는 소급이 불가라
        그 분이 **전 종목에 대해** 영구히 사라진다.

        인코딩으로 축을 가르지 않는 것도 여기서 못박는다 — `output` 이 없는 것과 빈
        것은 같은 벤더 상태(`rt_cd=0`, 쓸 게 없다)다."""
        collector = make_collector(body=body)
        result, records, manifest = collector.collect(request_for(31, ("069500",)), NOW)

        assert manifest["missing"] == ["069500"] and manifest["invalid"] == []
        assert result.status == "INCOMPLETE" and records == ()

    def test_만료를_만나면_두_캐시를_다_버리고_그_window_를_살린다(self):
        """⭐ ALPHA-889. 상주 전환(ALPHA-882)이 만료를 **반드시 만나는 것**으로 바꿨다.

        여기서 안 잡으면 자가치유가 아니다 — `StopFetch` 는 `MinuteWorkerLoop.tick` 의
        `except Exception` 에 삼켜져 WINDOW_FAILED 가 되고 루프는 계속 돈다. 즉 만료
        시점부터 15:30 까지 매 window 가 조용히 실패하고, iNAV 는 소급이 불가라 영구
        결손이다. 그래서 단언은 "안 죽는다"가 아니라 **그 window 가 실제로 채워진다**다.

        🔴 **두 캐시를 다 버리는지**가 이 테스트의 축이다. `auth.invalidate()` 만으로는
        컬렉터가 든 토큰 사본이 안 바뀌어 다음 호출도 같은 낡은 문자열을 보낸다.
        """
        etf_map = {"069500": "KR7069500007"}
        seen_tokens = []
        invalidated = []

        class ExpiringAuth:
            def __init__(self):
                self._n = 0

            def token(self):
                self._n += 1
                return f"TOKEN{self._n}"

            def invalidate(self):
                invalidated.append(1)

        def expire_once(our_etf_id, symbol, d1, d2, token):
            seen_tokens.append(token)
            if len(seen_tokens) == 1:
                raise ValueError("KIS rt_cd=1 msg_cd=EGW00121 msg1=기간이 만료된 token")
            return ROWS[our_etf_id]

        collector = make_collector(etf_map=etf_map, fetch=expire_once)
        collector.source.auth = ExpiringAuth()

        _, records, manifest = collector.collect(request_for(31), NOW)

        assert manifest["received"] == ["069500"], "재발급 뒤 그 window 가 채워져야 한다"
        assert manifest["missing"] == [] and records
        assert invalidated == [1], "공유 캐시(KisAuth) 를 버려야 SSM 까지 내려간다"
        # ⭐ 사본을 안 버리면 두 호출이 같은 토큰이다 — 그게 이 결함의 정체였다
        assert seen_tokens == ["TOKEN1", "TOKEN2"], \
            f"컬렉터 토큰 사본이 안 갱신됐다: {seen_tokens}"

    def test_4xx_로_오는_만료도_같은_경로다(self):
        """만료는 rt_cd 본문으로도, 4xx(StopFetch)로도 온다. 후자만 놓치면 그 갈래에서
        여전히 하루가 날아간다 — `kis_minute` 이 두 갈래를 다 막은 것과 같은 이유."""
        etf_map = {"069500": "KR7069500007"}
        calls = []

        class Auth:
            def token(self):
                return f"T{len(calls)}"

            def invalidate(self):
                pass

        def expire_4xx(our_etf_id, symbol, d1, d2, token):
            calls.append(token)
            if len(calls) == 1:
                exc = StopFetch("401")
                exc.body = "EGW00123 token expired"
                raise exc
            return ROWS[our_etf_id]

        collector = make_collector(etf_map=etf_map, fetch=expire_4xx)
        collector.source.auth = Auth()

        _, _, manifest = collector.collect(request_for(31), NOW)
        assert manifest["received"] == ["069500"]

    def test_재발급_뒤에도_만료면_전역_실패로_전파한다(self):
        """⭐ 리뷰가 뒤집은 자리다. 처음엔 `Outcome.MISSING` 으로 접었는데 **틀렸다.**

        재발급 뒤에도 만료면 그건 종목 축이 아니라 자격증명·시계다 — 다음 unit 도, 다음
        window 도 똑같이 만난다. MISSING 으로 접으면 전 종목이 매 분 missing 인
        INCOMPLETE 가 쌓이는데, 그 모양은 원장에서 "벤더가 안 준다"로 읽혀 원인이 우리
        쪽임을 가린다. 이 클래스가 "소스 전역 실패는 전파한다"로 못박은 축이고(Rule 12),
        4xx 갈래는 애초에 raise 하고 있어 **두 만료 경로의 계약이 갈려 있었다**.

        그리고 재발급은 여전히 1회여야 한다 — 무한 루프가 tick 을 먹으면 그날 나머지
        window 가 통째로 밀린다(만료보다 나쁘다). 전파와 1회 상한을 함께 고정한다.
        """
        etf_map = {"069500": "KR7069500007"}
        calls = []

        class Auth:
            def token(self):
                return "SAME"

            def invalidate(self):
                pass

        def always_expired(our_etf_id, symbol, d1, d2, token):
            calls.append(token)
            raise ValueError("KIS rt_cd=1 msg_cd=EGW00121 msg1=만료")

        collector = make_collector(etf_map=etf_map, fetch=always_expired)
        collector.source.auth = Auth()

        with pytest.raises(ValueError, match="EGW00121"):
            collector.collect(request_for(31), NOW)

        assert len(calls) == 2, f"1회만 재발급해야 한다 — 실제 {len(calls)}회 호출"
