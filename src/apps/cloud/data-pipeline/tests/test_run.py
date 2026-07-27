"""run 엔트리 테스트 — 증분 기본 날짜창 계산(스케줄러가 못 넣어주는 부분)."""

from datetime import datetime, timezone

import pytest

from data_pipeline.run import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_PRICE_LOOKBACK_DAYS,
    default_window,
    main,
)


def test_default_window_is_lookback_to_today_utc():
    # WHY: EventBridge Scheduler 는 정적 입력만 넣어 '어제~오늘'을 못 만든다 — 앱이
    #      런타임 시계로 증분 창을 계산해야 스케줄 실행이 그날 유입을 덮는다.
    now = datetime(2026, 7, 3, 5, 0, tzinfo=timezone.utc)
    from_date, to_date = default_window(now)
    assert to_date == "2026-07-03"
    assert from_date == "2026-07-02"  # DEFAULT_LOOKBACK_DAYS = 1


def test_lookback_default_is_one_day():
    # WHY: 소급 1일이면 직전 런과 경계가 겹쳐(하루) 유입 누락이 없다 — 겹침은 dedup 이 흡수.
    assert DEFAULT_LOOKBACK_DAYS == 1


def test_price_window_uses_wider_lookback():
    # WHY: 가격 EOD 는 주말·공휴일에 봉이 없어 소급 1일이면 월요일 런이 직전 거래일을
    #      놓친다 — 가격 증분 창은 더 넓은 소급을 써야 한다(겹치는 거래일은 raw 에 그대로
    #      보존되고 정체성 병합은 후속 canonical 소관 — ingest 단계는 dedup 하지 않는다).
    now = datetime(2026, 7, 6, 5, 0, tzinfo=timezone.utc)  # 월요일
    from_date, to_date = default_window(now, DEFAULT_PRICE_LOOKBACK_DAYS)
    assert to_date == "2026-07-06"
    assert from_date == "2026-07-01"  # 5일 소급 → 직전 금요일(7/3) 포함
    assert DEFAULT_PRICE_LOOKBACK_DAYS == 5


def test_normalize_disclosure_dispatches_step(tmp_path, monkeypatch):
    # WHY: normalize-disclosure 는 raw 를 읽는 정제 스텝이라 수집 창·소스 벤더 없이 곧장
    #      스텝으로 라우팅되고 --input-run-id 만 전달돼야 한다(normalize-price/news 와 동형).
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    called = {}

    from data_pipeline import run as run_mod

    def fake_run(storage, run_id, input_run_id):
        called["input_run_id"] = input_run_id
        return 0

    monkeypatch.setattr(run_mod.normalize_disclosure, "run", fake_run)
    assert main(["normalize-disclosure", "--input-run-id", "R7"]) == 0
    assert called == {"input_run_id": "R7"}


def test_normalize_disclosure_segment_dispatches_step(tmp_path, monkeypatch):
    # WHY: normalize-disclosure-segment 도 raw 를 읽는 정제 스텝이라 창·벤더 없이 스텝으로
    #      라우팅되고 --input-run-id 만 전달돼야 한다(normalize-disclosure 와 동형).
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    called = {}

    from data_pipeline import run as run_mod

    def fake_run(storage, run_id, input_run_id):
        called["input_run_id"] = input_run_id
        return 0

    monkeypatch.setattr(run_mod.normalize_disclosure_segment, "run", fake_run)
    assert main(["normalize-disclosure-segment", "--input-run-id", "R9"]) == 0
    assert called == {"input_run_id": "R9"}


def test_normalize_etf_dispatches_step(tmp_path, monkeypatch):
    # WHY: normalize-etf 도 raw 를 읽는 정제 스텝이라 수집 창·소스 벤더 없이 곧장 스텝으로
    #      라우팅되고 --input-run-id 만 전달돼야 한다(벤더는 raw 키 source= 로 판별).
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    called = {}

    from data_pipeline import run as run_mod

    def fake_run(storage, run_id, input_run_id):
        called["input_run_id"] = input_run_id
        return 0

    monkeypatch.setattr(run_mod.normalize_etf, "run", fake_run)
    assert main(["normalize-etf", "--input-run-id", "R11"]) == 0
    assert called == {"input_run_id": "R11"}


def test_krx_etf_client_timeout_exceeds_measured_endpoint_latency(monkeypatch):
    # WHY: KRX getJsonData 는 응답에 **10초를 넘게** 걸린다 — 2026-07-15 라이브 실측에서 같은
    #      세션·같은 요청이 timeout=10s 에선 TimeoutError, 45s 에선 12.4초에 성공했다. 기본값
    #      (PoliteClient timeout=10.0)을 그대로 쓰면 KRX ETF 수집이 **100% 실패**한다(ALPHA-368
    #      — 실제로 머지·배포된 채 아무도 실행하지 않아 잠복했다). 이 값을 실측 지연 아래로
    #      되돌리면 수집이 조용히 전량 실패로 돌아가므로 여기서 고정한다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    from data_pipeline import run as run_mod

    measured_latency_sec = 12.4  # 라이브 실측 응답 시간
    assert run_mod.KRX_ETF_TIMEOUT_SEC > measured_latency_sec

    captured = {}

    class _Spy(run_mod.KrxEtfSource):
        # 생성자에 인자가 늘어도(예: deadline_sec) 스파이가 깨지지 않게 그대로 넘긴다.
        def __init__(self, config, client, *args, **kwargs):
            captured["timeout"] = client.timeout
            super().__init__(config, client, *args, **kwargs)

    monkeypatch.setattr(run_mod, "KrxEtfSource", _Spy)
    monkeypatch.setattr(run_mod.ingest_raw_etf, "run", lambda *a, **k: 0)
    assert main(["ingest-raw-etf", "--source", "krx"]) == 0
    assert captured["timeout"] > measured_latency_sec, "KRX 수집이 타임아웃으로 전량 실패한다"


def test_kis_rejects_to_without_from(monkeypatch):
    # WHY: KIS inquire-daily 는 시작일(FID_INPUT_DATE_1)이 필수다 — --to 만 주면 빈 시작일로
    #      전 종목이 KIS 오류가 되어 무의미한 전량 실패가 된다. 한쪽만 준 창은 API 호출 전에
    #      fail-fast 로 거부해야 한다(증분=둘 다 미지정은 앱이 창을 채우므로 이 경로 아님).
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    with pytest.raises(SystemExit):
        main(["ingest-price-raw", "--source", "kis", "--to", "2026-06-30"])


def test_financial_rejects_unknown_source(monkeypatch):
    # WHY: 재무 수집 벤더 오타를 기본값으로 조용히 돌리면 의도와 다른 소스가 수집된다.
    #      알 수 없는 --source 는 API 호출 전에 fail-fast 해야 한다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    with pytest.raises(SystemExit):
        main(["ingest-raw-financial", "--source", "bogus"])


def test_dart_financial_requires_config(tmp_path, monkeypatch):
    # WHY: --source dart 를 명시했는데 설정 섹션이 없으면 기존 FMP 설정으로 대체하면 안 된다.
    #      DART 전용 설정 누락을 명확히 실패로 드러내야 한다.
    config = tmp_path / "sources.toml"
    config.write_text(
        """
[news.sources.fmp]
base_url = "https://fmp.example/news"

[price.source]
base_url = "https://fmp.example/price"

[financial.source]
base_url = "https://fmp.example/stable"

[targets]
symbols = ["005930"]
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    with pytest.raises(SystemExit):
        main(["ingest-raw-financial", "--source", "dart", "--config", str(config)])


def test_news_rejects_unknown_source(monkeypatch):
    # WHY: 뉴스 수집 벤더 오타를 기본 fmp 로 조용히 돌리면 의도와 다른 소스가 수집된다.
    #      알 수 없는 --source 는 API 호출 전에 fail-fast 해야 한다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    with pytest.raises(SystemExit):
        main(["ingest-raw", "--source", "bogus"])


def test_bigkinds_requires_config(tmp_path, monkeypatch):
    # WHY: --source bigkinds 를 명시했는데 설정 섹션이 없으면 기존 FMP 뉴스로 대체하면 안 된다.
    #      BigKinds 전용 설정 누락을 명확히 실패로 드러내야 한다.
    config = tmp_path / "sources.toml"
    config.write_text(
        """
[news.sources.fmp]
base_url = "https://fmp.example/news"

[price.source]
base_url = "https://fmp.example/price"

[targets]
symbols = ["005930"]
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    with pytest.raises(SystemExit):
        main(["ingest-raw", "--source", "bigkinds", "--config", str(config)])


def test_nav_shares_the_krx_etf_universe(monkeypatch):
    # WHY: NAV 는 자기 etf_map 을 두지 않고 krx_etf.source.etf_map 을 공유한다 — 맵을 복제하면
    #      한쪽만 갱신돼 구성종목과 NAV 의 수집 유니버스가 갈라진다(ALPHA-454 로 31종이 된 뒤
    #      NAV 는 옛 목록을 보는 식). 이 배선이 끊기면 컴파일은 되고 데이터만 어긋나므로
    #      값으로 고정한다. 창(--from/--to)도 함께 — 창이 안 넘어가면 백필이 조용히 죽는다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    from data_pipeline import run as run_mod

    captured = {}

    class _Spy(run_mod.KisNavSource):
        def __init__(self, config, etf_map, client, from_date=None, to_date=None):
            captured["etf_map"] = etf_map
            captured["window"] = (from_date, to_date)
            super().__init__(config, etf_map, client, from_date, to_date)

    monkeypatch.setattr(run_mod, "KisNavSource", _Spy)
    monkeypatch.setattr(run_mod.ingest_raw_etf, "run", lambda *a, **k: 0)
    assert main(["ingest-raw-nav", "--from", "2026-07-14", "--to", "2026-07-17"]) == 0

    settings = run_mod.load_settings(None)
    assert captured["etf_map"] == settings.krx_etf.source.etf_map
    assert captured["etf_map"], "NAV 유니버스가 비어 있으면 수집 대상이 0이다"
    assert captured["window"] == ("2026-07-14", "2026-07-17")


def _spy_inav(monkeypatch):
    """ingest-raw-inav 분기가 KisInavSource 에 넘긴 interval_sec 을 캡처한다."""
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    from data_pipeline import run as run_mod

    captured = {}

    class _Spy(run_mod.KisInavSource):
        def __init__(self, config, etf_map, client, interval_sec=None):
            captured["interval_sec"] = interval_sec
            captured["etf_map"] = etf_map
            super().__init__(config, etf_map, client, interval_sec=interval_sec)

    monkeypatch.setattr(run_mod, "KisInavSource", _Spy)
    monkeypatch.setattr(run_mod.ingest_raw_etf, "run", lambda *a, **k: 0)
    return run_mod, captured


def test_inav_interval_은_기본값과_명시값_모두_어댑터로_전달된다(monkeypatch):
    # WHY: 간격이 곧 조회 창(간격×30)이라 이 배선이 끊기면 폴링 주기와 창이 어긋나 갭이
    #      나는데, iNAV 는 소급 조회가 안 돼 그 갭이 영구 유실이다(ALPHA-555). 값으로 고정한다.
    run_mod, captured = _spy_inav(monkeypatch)

    assert main(["ingest-raw-inav"]) == 0
    assert captured["interval_sec"] == run_mod.DEFAULT_INTERVAL_SEC
    assert captured["etf_map"], "iNAV 유니버스가 비어 있으면 수집 대상이 0이다"

    assert main(["ingest-raw-inav", "--interval-sec", "10"]) == 0
    assert captured["interval_sec"] == 10


def test_inav_interval_0_은_조용히_기본값이_되지_않는다(monkeypatch):
    # WHY: 어댑터는 1 미만을 거부하는데, CLI 가 `or` 로 기본값을 채우면 0 이 falsy 라
    #      그 가드를 우회해 "60초로 수집 성공"이 된다 — 설정 오류가 성공으로 기록되는
    #      전형적 Rule 12 위반이다. 0 이 실제로 터지는지 값으로 고정한다.
    _spy_inav(monkeypatch)

    with pytest.raises(ValueError, match="interval_sec"):
        main(["ingest-raw-inav", "--interval-sec", "0"])


def test_inav_은_날짜창을_거부한다(monkeypatch):
    # WHY: 이 API 는 날짜·시각 지정을 무시하고 항상 최근 30행만 준다. 창을 조용히 무시하면
    #      갭을 메우려 --from/--to 를 준 운영자가 최근 30행을 받고 exit 0 을 보게 되고,
    #      소급이 영구 불가한 구간을 복구한 줄 착각한다 — 성공으로 위장된 실패다(Rule 12).
    _spy_inav(monkeypatch)

    for argv in (
        ["ingest-raw-inav", "--from", "2026-07-24"],
        ["ingest-raw-inav", "--to", "2026-07-24"],
        ["ingest-raw-inav", "--from", "2026-07-23", "--to", "2026-07-24"],
    ):
        with pytest.raises(SystemExit, match="--from/--to"):
            main(argv)


def _spy_tag_news(monkeypatch):
    """tag-news 분기가 tag_news.run 에 넘긴 (from_date, to_date) 를 캡처한다.
    tag-news 는 LLM_API_KEY 가 필수라 넣어 주고, complete_fn 은 클로저만 만들어 호출 안 한다."""
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    from data_pipeline import run as run_mod

    captured = {}

    def fake_run(storage, run_id, *, complete_fn, from_date, to_date, limit, concurrency):
        captured["window"] = (from_date, to_date)
        return 0

    monkeypatch.setattr(run_mod.tag_news, "run", fake_run)
    return run_mod, captured


def test_tag_news_window_days_prunes_to_recent_partitions(monkeypatch):
    # WHY: 일일 SFN 은 ASL 로 날짜 산술을 못 해 --window-days 만 넘긴다 — run 이 그걸 오늘−N
    #      창으로 번역해야 read=O(전체 코퍼스) 풀스캔(실측 17분)이 최근 파티션으로 좁혀진다
    #      (ALPHA-540). 배선이 끊기면 컴파일은 되고 매 런이 다시 전량 스캔하므로 값으로 고정.
    run_mod, captured = _spy_tag_news(monkeypatch)
    monkeypatch.setattr(run_mod, "default_window", lambda now, days: (f"from-{days}", f"to-{days}"))
    assert main(["tag-news", "--run-id", "R1", "--window-days", "3"]) == 0
    assert captured["window"] == ("from-3", "to-3")


def test_tag_news_explicit_window_overrides_window_days(monkeypatch):
    # WHY: 명시 --from/--to(백필)는 --window-days 보다 우선해야 한다 — 과거 구간 백필이
    #      조용히 최근 N일로 좁혀지면 그 구간이 영영 태깅되지 않는다.
    run_mod, captured = _spy_tag_news(monkeypatch)

    def _boom(*a, **k):
        raise AssertionError("명시 창이 있으면 default_window 를 부르면 안 된다")

    monkeypatch.setattr(run_mod, "default_window", _boom)
    assert main(["tag-news", "--run-id", "R", "--window-days", "3",
                 "--from", "2026-01-01", "--to", "2026-01-05"]) == 0
    assert captured["window"] == ("2026-01-01", "2026-01-05")


def test_tag_news_without_window_is_full_scan(monkeypatch):
    # WHY: --window-days 미주입(수동·백필 기본)은 풀스캔이어야 창 밖 미태깅·정정본 회수 경로가
    #      살아 있다(백로그 보전). from/to 가 None 으로 넘어가 tag_news 가 전체 파티션을 본다.
    run_mod, captured = _spy_tag_news(monkeypatch)

    def _boom(*a, **k):
        raise AssertionError("창 미주입은 풀스캔 — default_window 를 부르면 안 된다")

    monkeypatch.setattr(run_mod, "default_window", _boom)
    assert main(["tag-news", "--run-id", "R"]) == 0
    assert captured["window"] == (None, None)


def test_tag_news_negative_window_days_fails_loud(monkeypatch):
    # WHY: 음수 --window-days 는 default_window 를 (오늘+N, 오늘) 역전 창으로 만들어
    #      _partition_dates 가 전 파티션을 제외 → 0건 태깅 후 exit 0 으로 성공 위장한다.
    #      그건 Rule 12 위반이라 조용히 0건이 아니라 즉시 실패해야 한다(SFN 이 성공으로 오판 금지).
    run_mod, captured = _spy_tag_news(monkeypatch)

    def _boom(*a, **k):
        raise AssertionError("음수 창은 default_window 에 닿기 전에 거부돼야 한다")

    monkeypatch.setattr(run_mod, "default_window", _boom)
    with pytest.raises(SystemExit):
        main(["tag-news", "--run-id", "R", "--window-days", "-3"])
    assert "window" not in captured, "음수 창은 tag_news.run 에 닿으면 안 된다"

    # 명시 --from 이 함께 와 창 계산이 무시되는 경로에서도 음수는 거부한다 — 잘못된 입력이
    # "명시 창이 이겼으니 괜찮다"로 조용히 삼켜지면 안 된다(Rule 12, 가드는 분기 밖에 있어야).
    with pytest.raises(SystemExit):
        main(["tag-news", "--run-id", "R", "--from", "2026-01-01", "--window-days", "-3"])
    assert "window" not in captured


def test_deadline_rejected_where_it_is_ignored(monkeypatch):
    # WHY: `--deadline-sec` 는 KRX ETF 만 소비한다. 다른 스텝에서 조용히 무시되면 운영자가
    #      상한이 걸렸다고 오인하고(있다고 믿는데 안 걸린다), SFN 이 엉뚱한 브랜치에 상한을
    #      준 배선 오류도 안 드러난다(Rule 12). 조기 반환 스텝을 포함해 검증 위치를 고정한다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    from data_pipeline import run as run_mod

    for argv in (
        ["ingest-raw-etf", "--source", "fmp", "--deadline-sec", "300"],
        ["ingest-raw-etf", "--deadline-sec", "300"],  # --source 생략 = fmp 경로
        ["ingest-price-raw", "--source", "kis", "--deadline-sec", "300"],
        ["normalize-etf", "--deadline-sec", "300"],
        ["tag-news", "--deadline-sec", "300"],
    ):
        with pytest.raises(SystemExit) as err:
            run_mod.main(argv)
        assert "--deadline-sec" in str(err.value)


def test_deadline_rejects_non_positive_and_nan(monkeypatch):
    # WHY: 0·음수는 첫 대상도 시도하기 전에 상한에 걸려 매 런이 0건 수집으로 끝난다 —
    #      상한이 수집을 통째로 막는다. NaN 은 반대로 `경과 >= nan` 이 항상 False 라 상한이
    #      **통째로 사라진다**. 둘 다 "있는데 안 걸린다/다 걸린다"라 즉시 실패가 맞다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    from data_pipeline import run as run_mod

    for bad in ("0", "-300", "nan"):
        with pytest.raises(SystemExit) as err:
            run_mod.main(["ingest-raw-etf", "--source", "krx", "--deadline-sec", bad])
        assert "0 보다 큰 유한한 값" in str(err.value)
