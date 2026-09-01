"""run 엔트리 테스트 — 증분 기본 날짜창 계산(스케줄러가 못 넣어주는 부분)."""

import pathlib
from datetime import datetime, timezone

import pytest

from data_pipeline import run as run_mod
from data_pipeline.run import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_PRICE_LOOKBACK_DAYS,
    default_window,
    main,
)


def test_default_window_is_lookback_to_today_in_given_tz():
    # WHY: EventBridge Scheduler 는 정적 입력만 넣어 '어제~오늘'을 못 만든다 — 앱이
    #      런타임 시계로 증분 창을 계산해야 스케줄 실행이 그날 유입을 덮는다.
    now = datetime(2026, 7, 3, 5, 0, tzinfo=timezone.utc)
    from_date, to_date = default_window(now)
    assert to_date == "2026-07-03"
    assert from_date == "2026-07-02"  # DEFAULT_LOOKBACK_DAYS = 1


def test_window_calendar_tz_is_declared_per_step_and_vendor():
    # WHY(ALPHA-883): 창의 날짜는 프로세스 시계가 아니라 **벤더 달력**이다 — 그 날짜 문자열이
    #      그대로 벤더 질의에 실린다(BigKinds startDate/endDate). 한쪽으로 통일하면 반드시
    #      한쪽이 틀린다: 전부 UTC 면 한국 벤더가 09:00 KST 이전 슬롯에서 하루 밀리고, 전부
    #      KST 면 FMP 가 민다. **한 표가 두 방향을 다 막는지**를 값으로 든다.
    assert run_mod.window_calendar_tz("ingest-raw", "bigkinds") == run_mod.KST
    assert run_mod.window_calendar_tz("ingest-raw", "fmp") == timezone.utc
    # --source 미지정이면 그 스텝의 기본 벤더를 따라야 한다(분기가 읽는 `_VENDOR_SPLIT_STEPS`).
    assert run_mod.window_calendar_tz("ingest-raw", None) == timezone.utc
    assert run_mod.window_calendar_tz("ingest-price-raw", None) == timezone.utc
    assert run_mod.window_calendar_tz("ingest-price-raw", "kis") == run_mod.KST
    # yahoo 는 미국 서비스지만 index_map 이 ^KS11·^KQ11 이라 **한국 달력**이다 — 벤더 국적이
    # 아니라 그 데이터가 어느 시장의 날짜인가가 기준임을 여기서 못박는다.
    assert run_mod.window_calendar_tz("ingest-price-raw", "yahoo") == run_mod.KST
    for step in ("load-disclosure", "ingest-raw-disclosure",
                 "ingest-raw-investor", "ingest-raw-nav", "ingest-raw-inav"):
        assert run_mod.window_calendar_tz(step, None) == run_mod.KST, step
    # 벤더가 안 갈리는 스텝은 무의미한 --source 를 무시한다(분기도 안 본다) — 그것 때문에
    # 창 계산이 죽으면 이 변경이 없던 실패를 만든다.


def test_undeclared_window_calendar_fails_loud():
    # WHY(ALPHA-883): 이 레포엔 축이 다른 관례가 둘이라(순간=UTC, 시장 날짜=KST) 어느 쪽을
    #      기본으로 삼아도 다음 사람이 반대로 읽는다. 기본을 두면 새 스텝이 조용히 한쪽으로
    #      떨어지고 그 창은 하루가 밀린 채 **성공**한다 — 창을 쓰는 스텝이 늘면 여기서 죽어야 한다.
    with pytest.raises(SystemExit, match="달력이 선언되지 않았다"):
        run_mod.window_calendar_tz("ingest-raw-newvendor", None)


def test_unknown_source_is_diagnosed_as_source_not_calendar():
    # WHY(ALPHA-883): 오타난 `--source` 를 "달력 미선언"으로 진단하면 시킨 대로 표에 넣어도
    #      아무것도 안 고쳐지고 그제서야 분기의 진짜 에러를 본다. 게다가 창 계산이 분기보다
    #      앞이라 그 진단이 `--from/--to` 유무로 갈린다 — 같은 오타에 다른 메시지가 나온다.
    #      벤더가 갈리는 스텝에선 표의 키 집합이 곧 벤더 화이트리스트다.
    with pytest.raises(SystemExit, match=r"알 수 없는 --source: quandl \(bigkinds\|fmp\)"):
        run_mod.window_calendar_tz("ingest-raw", "quandl")


def test_branch_vendor_default_comes_from_the_calendar_table():
    # WHY(ALPHA-883): 분기의 기본 벤더와 달력 표의 기본 벤더가 **두 벌**이면 한쪽만 바뀌는
    #      순간 창은 이 벤더로, 질의는 저 벤더로 나간다 — 리뷰 변이 실측에서 분기 쪽만 바꿔도
    #      전 스위트가 초록이었다. 리터럴을 지워 사실을 하나로 만든 것을 여기서 고정한다.
    #      ⚠️ 리터럴 부재는 단언하지 않는다 — `ingest-raw-financial`·`ingest-raw-etf` 는 창을
    #      아예 안 써서(창 계산 앞에서 분기한다) 자기 리터럴을 갖는 게 맞다.
    src = (pathlib.Path(run_mod.__file__)).read_text(encoding="utf-8")
    for step in run_mod._VENDOR_SPLIT_STEPS:
        assert f'vendor = args.source or _VENDOR_SPLIT_STEPS["{step}"]' in src, step


def test_window_does_not_move_for_todays_slot_times():
    # WHY(ALPHA-883): 이 변경은 **잠복 결함만** 없애야 하고 09:00 KST 이후 슬롯의 동작은 한 톨도
    #      바뀌면 안 된다. 그 시각대는 UTC 날짜와 KST 날짜가 우연히 같다 — 그 우연을 값으로
    #      고정해 둔다. 여기가 빨개지면 그날 도는 레인의 수집 창이 실제로 움직인 것이다.
    # ⚠️ ALPHA-893 이 뉴스 08:10 슬롯을 넣으면서 **"모든 슬롯이 09:00 이후"는 더 이상 사실이
    #      아니다.** 09:00 이전 슬롯은 UTC 와 KST 가 갈리는 것이 **정상**이라 이 표에 넣으면
    #      안 된다 — 그쪽은 아래 `test_kst_vendor_window_covers_today_before_0900_kst` 소관이다.
    #      이 표는 "09:00 이후 슬롯" 집합의 불변을 지키는 자리로 남는다.
    for label, kst_hour, kst_minute in [
        ("뉴스 day-close", 23, 50),
        ("공시 첫 슬롯(경계)", 9, 0), ("시장 EOD", 15, 40), ("장중 수급 첫 슬롯", 9, 35),
    ]:
        now_kst = datetime(2026, 7, 3, kst_hour, kst_minute, tzinfo=run_mod.KST)
        assert default_window(now_kst) == default_window(now_kst.astimezone(timezone.utc)), label


def test_kst_vendor_window_covers_today_before_0900_kst():
    # WHY(ALPHA-883): 09:00 KST 이전은 **전날 UTC 날짜**로 떨어진다(KST=UTC+9). 08:10 슬롯의
    #      창이 [D-2, D-1] 이 되면 **그날 기사를 한 건도 안 가져온다** — 8시간 전 00:10 런과
    #      완전히 같은 창을 다시 긁을 뿐이고, 에러도 안 난다. 조용한 헛돎을 막는다.
    #      ⚠️ ALPHA-893 이 그 08:10 슬롯을 실제로 넣었고, ALPHA-905 가 day-close 를 00:10 으로
    #      옮기며 **두 슬롯 모두** 09:00 이전이 됐다 — 이 테스트는 이제 가정이 아니라 그 레인의
    #      **모든 런**을 지킨다. 00:10 은 UTC 로 전날 15:10 이라 어긋나면 하루를 통째로 못 닫는다.
    at_0810_kst = datetime(2026, 7, 3, 8, 10, tzinfo=run_mod.KST)
    news = default_window(at_0810_kst.astimezone(
        run_mod.window_calendar_tz("ingest-raw", "bigkinds")))
    assert news == ("2026-07-02", "2026-07-03"), "그날(07-03)이 창에 없으면 장전 런이 헛돈다"
    # 같은 순간에 FMP 는 미국 달력이라 종전 그대로여야 한다 — 한쪽을 고치며 다른 쪽을 밀지 않는다.
    fmp = default_window(at_0810_kst.astimezone(
        run_mod.window_calendar_tz("ingest-raw", "fmp")))
    assert fmp == ("2026-07-01", "2026-07-02")
    assert news != fmp, "두 달력이 같은 답을 내면 이 함수가 아무것도 안 가르고 있다"


class _WindowComputed(Exception):
    """창 계산 시점에 CLI 를 끊는 sentinel — 그 뒤(설정·네트워크)는 이 축과 무관하다."""


def test_cli_passes_vendor_calendar_to_window(monkeypatch):
    # WHY(ALPHA-883): `window_calendar_tz` 만 테스트하면 **호출부가 UTC 로 돌아가도 초록**이다
    #      — 실제로 변이에서 그랬다(`:795` 만 되돌렸는데 전 스위트 통과). 달력을 고르는 것과
    #      그걸 창 계산에 실제로 넘기는 것은 다른 사실이라 따로 든다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    seen = {}

    def fake_window(now, lookback_days=None):
        seen["tz"] = now.tzinfo
        raise _WindowComputed

    monkeypatch.setattr(run_mod, "default_window", fake_window)
    for argv, expected, label in [
        (["ingest-raw", "--source", "bigkinds", "--run-id", "R1"], run_mod.KST, "BigKinds=KST"),
        (["ingest-raw", "--source", "fmp", "--run-id", "R1"], timezone.utc, "FMP=미국 달력"),
        (["load-disclosure", "--run-id", "R1", "--window-days", "3"], run_mod.KST, "DART=KST"),
    ]:
        seen.clear()
        with pytest.raises(_WindowComputed):
            main(argv)
        assert seen["tz"] == expected, label


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


def test_ETF_NAV_정제_호환이미지는_부분성공을_구_SFN에_0으로_반환한다(monkeypatch):
    # WHY(ALPHA-1042): 자동 이미지 CD가 exit-2 게이트보다 먼저 배포돼도 producer의 성공
    # winner가 loader까지 흘러야 한다. 실패 증거는 manifest·quality에 남고 CLI만 한시 매핑한다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    from data_pipeline import run as run_mod

    monkeypatch.setattr(run_mod.normalize_etf_nav, "run", lambda *args: 2)

    assert main(["normalize-etf-nav", "--run-id", "R", "--input-run-id", "R"]) == 0


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

    # 벤더 어휘는 분 단위다(60:1분·180:3분·…·7200:120분) — 어휘 안의 값으로 고정한다.
    assert main(["ingest-raw-inav", "--interval-sec", "180"]) == 0
    assert captured["interval_sec"] == 180


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

    def fake_run(storage, run_id, *, complete_fn, input_run_id, from_date, to_date,
                 limit, concurrency):
        captured["window"] = (from_date, to_date)
        captured["input_run_id"] = input_run_id
        return 0

    monkeypatch.setattr(run_mod.tag_news, "run", fake_run)
    return run_mod, captured


def test_tag_news_forwards_normalize_manifest_scope(monkeypatch):
    # WHY(ALPHA-1032): SFN의 run_id가 사라지면 정상 경로가 다시 날짜 추측·풀스캔으로 퇴행한다.
    run_mod, captured = _spy_tag_news(monkeypatch)
    assert main(["tag-news", "--run-id", "R", "--input-run-id", "N1"]) == 0
    assert captured["input_run_id"] == "N1"
    assert captured["window"] == (None, None)


def test_tag_news_requires_explicit_scope(monkeypatch):
    _spy_tag_news(monkeypatch)
    with pytest.raises(SystemExit, match="--input-run-id, --from/--to, --all"):
        main(["tag-news", "--run-id", "R"])


def test_tag_news_explicit_all_preserves_full_recovery(monkeypatch):
    _run_mod, captured = _spy_tag_news(monkeypatch)
    assert main(["tag-news", "--run-id", "R", "--all"]) == 0
    assert captured["input_run_id"] is None and captured["window"] == (None, None)


def _spy_load_disclosure(monkeypatch):
    """load-disclosure 분기가 넘긴 (from_date, to_date) 를 캡처한다. DB 는 안 뜬다."""
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    from data_pipeline import run as run_mod

    captured = {}

    def fake_run(storage, run_id, *, db, input_run_id, from_date, to_date,
                 bootstrap, pending_only):
        captured["window"] = (from_date, to_date)
        captured["input_run_id"] = input_run_id
        captured["bootstrap"] = bootstrap
        captured["pending_only"] = pending_only
        return 0

    monkeypatch.setattr(run_mod.load_disclosure, "run", fake_run)
    monkeypatch.setattr(run_mod, "db_config_from_env", lambda db: db)
    return run_mod, captured


def test_load_disclosure_window_days_prunes_report_date_partitions(monkeypatch):
    # WHY(ALPHA-721): 장중 공시 레인은 하루 10슬롯이고 이 로더는 창 미지정이면 canonical
    #      report_date **전체 스캔**이라, 배선이 없으면 그 풀스캔이 슬롯마다 곱해진다
    #      (news-load-fullscan-problem 과 같은 축). ASL 은 날짜 산술을 못 해 --window-days 만
    #      넘기므로 run 이 창으로 번역해야 한다 — 끊겨도 컴파일은 되고 매 슬롯이 전량 스캔하니
    #      값으로 고정한다.
    run_mod, captured = _spy_load_disclosure(monkeypatch)
    monkeypatch.setattr(run_mod, "default_window", lambda now, days: (f"from-{days}", f"to-{days}"))
    assert main(["load-disclosure", "--run-id", "R1", "--window-days", "3"]) == 0
    assert captured["window"] == ("from-3", "to-3")
    assert captured["input_run_id"] is None

    assert main(["load-disclosure", "--run-id", "R1", "--input-run-id", "N1",
                 "--window-days", "3"]) == 0
    assert captured["input_run_id"] == "N1"


def test_load_disclosure_explicit_recovery_scopes_are_fail_loud(monkeypatch):
    # WHY(ALPHA-1055): 풀스캔을 암묵 기본값으로 남기면 운영 오배선이 canonical 전체를 읽는다.
    # 날짜/전체 bootstrap과 pending-only는 서로 다른 복구 계약이라 플래그로 고정한다.
    run_mod, captured = _spy_load_disclosure(monkeypatch)

    def _boom(*a, **k):
        raise AssertionError("명시 창·창 미주입 경로에서 default_window 를 부르면 안 된다")

    monkeypatch.setattr(run_mod, "default_window", _boom)
    assert main(["load-disclosure", "--run-id", "R",
                 "--from", "2026-01-01", "--to", "2026-01-05"]) == 0
    assert captured["window"] == ("2026-01-01", "2026-01-05")
    assert captured["bootstrap"] is True and captured["pending_only"] is False

    assert main(["load-disclosure", "--run-id", "R", "--all"]) == 0
    assert captured["window"] == (None, None) and captured["bootstrap"] is True

    assert main(["load-disclosure", "--run-id", "R", "--pending-only"]) == 0
    assert captured["pending_only"] is True and captured["bootstrap"] is False

    with pytest.raises(SystemExit, match="중 하나가 필요하다"):
        main(["load-disclosure", "--run-id", "R"])
    with pytest.raises(SystemExit, match="함께 쓸 수 없다"):
        main(["load-disclosure", "--run-id", "R", "--all", "--pending-only"])
    with pytest.raises(SystemExit, match="함께 쓸 수 없다"):
        main(["load-disclosure", "--run-id", "R", "--input-run-id", "N1",
              "--from", "2026-01-01", "--to", "2026-01-05"])
    with pytest.raises(SystemExit, match="함께 써야 한다"):
        main(["load-disclosure", "--run-id", "R", "--from", "2026-01-01"])


def test_backfill_normalize_disclosure_defaults_to_all_raw_and_accepts_filing_window(monkeypatch):
    """백필 기본값이 최근 N일이면 보관 raw의 과거 공시가 영구 누락된다. 명시 창은 그대로
    전달하고 미지정은 `(None, None)`으로 전체 raw 재처리를 요청해야 한다."""
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    captured = []

    def fake_run(storage, run_id, *, db, from_date, to_date):
        captured.append((from_date, to_date))
        return 0

    monkeypatch.setattr(run_mod.backfill_disclosure, "run", fake_run)
    monkeypatch.setattr(run_mod, "db_config_from_env", lambda db: db)

    assert main(["backfill-normalize-disclosure", "--run-id", "B1"]) == 0
    assert main(["backfill-normalize-disclosure", "--run-id", "B2",
                 "--from", "2026-01-01", "--to", "2026-06-30"]) == 0
    assert captured == [(None, None), ("2026-01-01", "2026-06-30")]

    with pytest.raises(SystemExit):
        main(["backfill-disclosure", "--run-id", "B3"])


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


def test_deadline_reaches_the_krx_source(monkeypatch):
    # WHY: 파싱과 배선은 다른 일이다. `deadline_sec=args.deadline_sec` 한 줄이 지워져도
    #      CLI 는 값을 받아들이고 어댑터만 조용히 무제한으로 돈다 — SFN 이 300초를 줬는데
    #      25분을 도는 상태가 되고, 로그만 봐서는 상한이 걸린 줄 안다(edge-review 지적).
    #      어댑터 단위 테스트는 값을 직접 세팅하므로 이 구간을 못 덮는다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    from data_pipeline import run as run_mod

    captured = {}

    class _Spy(run_mod.KrxEtfSource):
        def __init__(self, config, client, *args, **kwargs):
            super().__init__(config, client, *args, **kwargs)
            captured["deadline_sec"] = self.deadline_sec

    monkeypatch.setattr(run_mod, "KrxEtfSource", _Spy)
    monkeypatch.setattr(run_mod.ingest_raw_etf, "run", lambda *a, **k: 0)

    assert main(["ingest-raw-etf", "--source", "krx", "--deadline-sec", "123"]) == 0
    assert captured["deadline_sec"] == 123.0

    # 미지정이면 무제한이 어댑터까지 그대로 가야 한다(기본 동작 불변).
    captured.clear()
    assert main(["ingest-raw-etf", "--source", "krx"]) == 0
    assert captured["deadline_sec"] is None


def _spy_assemble(monkeypatch):
    """assemble-events 분기가 assemble_events.run 에 넘긴 창을 캡처한다(_spy_tag_news 와 동형).
    db 는 스파이가 안 쓰므로 db_config_from_env 를 항등으로 눌러 설정 결합을 끊는다."""
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    from data_pipeline import run as run_mod

    captured = {}

    def fake_run(storage, run_id, *, db, complete_fn, from_date, to_date, window_days,
                 concurrency):
        captured["window"] = (from_date, to_date, window_days)
        return 0

    monkeypatch.setattr(run_mod.assemble_events, "run", fake_run)
    monkeypatch.setattr(run_mod, "db_config_from_env", lambda base: base)
    return run_mod, captured


def test_assemble_window_days_reaches_step(monkeypatch):
    # WHY: 파싱과 배선은 다른 일이다 — window_days 전달 한 줄이 지워져도 CLI 는 값을 받고
    #      assemble 만 조용히 '실행 시점 오늘 하루'로 돈다. 자정 crossing 방지(ALPHA-592)가
    #      이 배선 하나에 달렸다(2026-07-28 00:03 read=0 실사고의 재발 방지 축).
    run_mod, captured = _spy_assemble(monkeypatch)
    assert main(["assemble-events", "--run-id", "R", "--window-days", "1"]) == 0
    assert captured["window"] == (None, None, 1)


def test_assemble_explicit_window_beats_window_days(monkeypatch):
    # WHY: 명시 --from/--to(백필·회수)는 창 폭보다 우선해야 한다 — 회수 실행이 조용히 최근
    #      N일로 좁혀지면 그 구간이 영영 조립되지 않는다.
    run_mod, captured = _spy_assemble(monkeypatch)
    assert main(["assemble-events", "--run-id", "R", "--window-days", "1",
                 "--from", "2026-07-27", "--to", "2026-07-27"]) == 0
    assert captured["window"] == ("2026-07-27", "2026-07-27", 1)


def test_window_days_rejected_on_non_consuming_step(monkeypatch):
    # WHY: 소비하지 않는 스텝이 조용히 받으면 운영자가 창이 걸렸다고 오인하고 SFN 배선
    #      오류(엉뚱한 브랜치에 창을 준 것)도 안 드러난다 — --deadline-sec 과 같은 가드(Rule 12).
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    with pytest.raises(SystemExit) as err:
        main(["normalize-price", "--run-id", "R", "--window-days", "1"])
    assert "assemble-events·load-disclosure" in str(err.value)


def test_assemble_negative_window_days_fails_loud(monkeypatch):
    # WHY: 음수 창은 역전 창(오늘+N, 오늘)이 되어 전 파티션을 제외 → 0건 조립을 exit 0
    #      성공으로 위장한다(공통 가드가 막는다).
    run_mod, captured = _spy_assemble(monkeypatch)
    with pytest.raises(SystemExit) as err:
        main(["assemble-events", "--run-id", "R", "--window-days", "-1"])
    assert "음수" in str(err.value)
    assert "window" not in captured  # 스텝까지 못 간다


def test_absurd_window_days_fails_loud(monkeypatch):
    # WHY: 상한 없는 창(예 800000)은 date 연산 하한을 넘겨 OverflowError 로 죽는데, 그
    #      크래시는 collection_log 기록 밖이라 감사 레코드 없이 매 런 실패한다 — 파싱 직후
    #      fail-loud 가 맞다(풀스캔은 미지정·--from/--to 가 정규 경로).
    run_mod, captured = _spy_assemble(monkeypatch)
    with pytest.raises(SystemExit) as err:
        main(["assemble-events", "--run-id", "R", "--window-days", "800000"])
    assert "상한" in str(err.value)
    assert "window" not in captured


def test_load_documents_requires_an_explicit_scope(monkeypatch):
    # WHY(ALPHA-1031): 범위 누락이 암묵적 canonical 풀스캔으로 성공하면 SFN 배선 회귀가
    #      데이터가 쌓일수록 비싸지는 형태로 숨는다. 전체 복구도 --all로 의도를 드러내야 한다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    with pytest.raises(SystemExit, match="--input-run-id, --from/--to, --all"):
        main(["load-documents", "--run-id", "R"])


def test_load_price_daily_requires_exactly_one_explicit_scope(monkeypatch):
    # WHY(ALPHA-1038): SFN의 run 계보가 빠져도 암묵적 canonical 풀스캔으로 성공하면 배선
    # 회귀가 데이터가 쌓일수록 비싸지는 형태로 숨는다. 범위 혼합도 모호하므로 거부한다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    for argv in (
        ["load-price-daily", "--run-id", "R"],
        ["load-price-daily", "--run-id", "R", "--input-run-id", "N1", "--all"],
        ["load-price-daily", "--run-id", "R", "--input-run-id", "N1",
         "--from", "2026-08-20"],
    ):
        with pytest.raises(SystemExit, match="--input-run-id, --from/--to, --all"):
            main(argv)

    for bound in ("--from", "--to"):
        with pytest.raises(SystemExit, match="함께 써야"):
            main(["load-price-daily", "--run-id", "R", bound, "2026-08-20"])


def test_load_price_daily_forwards_manifest_scope_and_preserves_explicit_all(monkeypatch):
    # WHY(ALPHA-1038): 정상 SFN은 실제 normalize run을 그대로 넘기고, 전체 복구는 운영자가
    # --all을 직접 쓴 경우에만 범위 없음으로 전달해야 한다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    captured = []
    monkeypatch.setattr(
        run_mod.load_price_daily, "run",
        lambda *args, **kwargs: captured.append(kwargs) or 0,
    )
    monkeypatch.setattr(run_mod, "db_config_from_env", lambda _cfg: object())

    assert main(["load-price-daily", "--run-id", "R", "--input-run-id", "N1"]) == 0
    assert captured[-1]["input_run_id"] == "N1"
    assert main(["load-price-daily", "--run-id", "R", "--all"]) == 0
    assert captured[-1]["input_run_id"] is None
    assert captured[-1]["from_date"] is None and captured[-1]["to_date"] is None


def test_load_etf_flow_requires_exactly_one_explicit_scope(monkeypatch):
    # WHY(ALPHA-1041): 정상 SFN의 run 계보가 빠져도 암묵적 canonical 풀스캔으로 성공하면
    # 데이터가 쌓일수록 비싸지는 배선 회귀가 숨는다. 혼합·반쪽 날짜 범위도 거부한다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    for argv in (
        ["load-etf-flow", "--run-id", "R"],
        ["load-etf-flow", "--run-id", "R", "--input-run-id", "N1", "--all"],
        ["load-etf-flow", "--run-id", "R", "--input-run-id", "N1",
         "--from", "2026-08-20", "--to", "2026-08-21"],
    ):
        with pytest.raises(SystemExit, match="--input-run-id, --from/--to, --all"):
            main(argv)

    for bound in ("--from", "--to"):
        with pytest.raises(SystemExit, match="함께 써야"):
            main(["load-etf-flow", "--run-id", "R", bound, "2026-08-20"])


def test_load_etf_nav_호환단계에서도_혼합_scope는_거부한다(monkeypatch):
    # WHY(ALPHA-1043): 첫 이미지가 구 SFN의 scope 누락만 한시적으로 받아도, 서로 다른
    # scope 혼합까지 허용하면 대상 범위의 우선순위가 모호해진다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    for argv in (
        ["load-etf-nav", "--run-id", "R", "--input-run-id", "N1", "--all"],
        ["load-etf-nav", "--run-id", "R", "--input-run-id", "N1",
         "--from", "2026-08-20", "--to", "2026-08-21"],
    ):
        with pytest.raises(SystemExit, match="함께 쓸 수 없다"):
            main(argv)
    for bound in ("--from", "--to"):
        with pytest.raises(SystemExit, match="함께 써야"):
            main(["load-etf-nav", "--run-id", "R", bound, "2026-08-20"])


def test_load_etf_nav_forwards_manifest_and_recovery_scopes(monkeypatch):
    # WHY(ALPHA-1043): 호환 이미지가 구 SFN의 무인자 호출을 보존하면서도 새 manifest와
    # 명시된 날짜/전체 복구 범위를 정확히 전달해야 배포 순서를 안전하게 분리할 수 있다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    captured = []
    monkeypatch.setattr(
        run_mod.load_etf_nav, "run", lambda *args, **kwargs: captured.append(kwargs) or 0,
    )
    monkeypatch.setattr(run_mod, "db_config_from_env", lambda _cfg: object())
    assert main(["load-etf-nav", "--run-id", "R"]) == 0
    assert captured[-1]["input_run_id"] is None
    assert captured[-1]["from_date"] is None and captured[-1]["to_date"] is None
    assert main(["load-etf-nav", "--run-id", "R", "--input-run-id", "N1"]) == 0
    assert captured[-1]["input_run_id"] == "N1"
    assert main(["load-etf-nav", "--run-id", "R", "--from", "2026-08-20",
                 "--to", "2026-08-21"]) == 0
    assert (captured[-1]["from_date"], captured[-1]["to_date"]) == (
        "2026-08-20", "2026-08-21",
    )
    assert main(["load-etf-nav", "--run-id", "R", "--all"]) == 0
    assert captured[-1]["input_run_id"] is None


@pytest.mark.parametrize("argv", [
    ["--from", "2026-08-32", "--to", "2026-08-32"],
    ["--from", "2026-08-20", "--to", "2026-08-10"],
])
def test_load_etf_nav_rejects_invalid_recovery_window(monkeypatch, argv):
    # WHY(ALPHA-1043): 불량·역전 날짜가 0건 성공이면 수동 복구 완료로 오인된다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    with pytest.raises(SystemExit):
        main(["load-etf-nav", "--run-id", "R", *argv])


def test_load_etf_flow_forwards_manifest_and_recovery_scopes(monkeypatch):
    # WHY(ALPHA-1041): 정상 경로는 NormalizeInvestor의 정확한 run_id를 전달해야 하고, 기존
    # 날짜/전체 복구는 운영자가 명시한 경우에만 같은 인자로 보존돼야 한다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    captured = []
    monkeypatch.setattr(
        run_mod.load_etf_flow, "run",
        lambda *args, **kwargs: captured.append(kwargs) or 0,
    )
    monkeypatch.setattr(run_mod, "db_config_from_env", lambda _cfg: object())

    assert main(["load-etf-flow", "--run-id", "R", "--input-run-id", "N1"]) == 0
    assert captured[-1]["input_run_id"] == "N1"
    assert main(["load-etf-flow", "--run-id", "R", "--from", "2026-08-20",
                 "--to", "2026-08-21"]) == 0
    assert captured[-1]["from_date"] == "2026-08-20"
    assert captured[-1]["to_date"] == "2026-08-21"
    assert main(["load-etf-flow", "--run-id", "R", "--all"]) == 0
    assert captured[-1]["input_run_id"] is None
    assert captured[-1]["from_date"] is None and captured[-1]["to_date"] is None


@pytest.mark.parametrize("argv", [
    ["--from", "2026-08-32", "--to", "2026-08-32"],
    ["--from", "2026-08-20", "--to", "2026-08-10"],
])
def test_load_etf_flow_rejects_invalid_recovery_window(monkeypatch, argv):
    # WHY(ALPHA-1041): 불량·역전 창이 0건 성공이면 수동 복구 완료로 오인된다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    with pytest.raises(SystemExit):
        main(["load-etf-flow", "--run-id", "R", *argv])


def test_load_price_triggers_forwards_manifest_scope_and_preserves_explicit_all(monkeypatch):
    # WHY(ALPHA-1039): 정상 SFN의 run_id가 사라지면 트리거가 canonical 가격·holdings 전체
    # 탐색으로 돌아간다. 전체 복구는 운영자가 --all을 직접 쓴 경우에만 허용한다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    captured = []
    monkeypatch.setattr(
        run_mod.load_price_triggers, "run",
        lambda *args, **kwargs: captured.append(kwargs) or 0,
    )
    monkeypatch.setattr(run_mod, "db_config_from_env", lambda _cfg: object())

    assert main(["load-price-triggers", "--run-id", "R", "--input-run-id", "N1"]) == 0
    assert captured[-1]["input_run_id"] == "N1"
    assert main(["load-price-triggers", "--run-id", "R", "--all"]) == 0
    assert captured[-1]["input_run_id"] is None
    assert captured[-1]["from_date"] is None and captured[-1]["to_date"] is None


def test_load_price_triggers_requires_exactly_one_explicit_scope(monkeypatch):
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    for argv in (
        ["load-price-triggers", "--run-id", "R"],
        ["load-price-triggers", "--run-id", "R", "--input-run-id", "N1", "--all"],
        ["load-price-triggers", "--run-id", "R", "--input-run-id", "N1",
         "--from", "2026-08-20", "--to", "2026-08-21"],
    ):
        with pytest.raises(SystemExit, match="--input-run-id, --from/--to, --all"):
            main(argv)


@pytest.mark.parametrize("argv", [
    ["--from", "2026-08-32", "--to", "2026-08-32"],
    ["--from", "2026-08-20"],
    ["--from", "2026-08-20", "--to", "2026-08-10"],
])
def test_load_price_triggers_rejects_invalid_recovery_window(monkeypatch, argv):
    # 불량 창이 0건 성공으로 끝나면 수동 복구 완료로 오인된다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    with pytest.raises(SystemExit):
        main(["load-price-triggers", "--run-id", "R", *argv])


@pytest.mark.parametrize("argv", [
    ["--from", "2026-08-32", "--to", "2026-08-32"],
    ["--from", "2026-08-20", "--to", "2026-08-10"],
])
def test_load_price_daily_rejects_invalid_recovery_window(monkeypatch, argv):
    # WHY(ALPHA-1038): 불량·역전 문자열이 0건 성공이면 복구 완료로 오인된다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    with pytest.raises(SystemExit):
        main(["load-price-daily", "--run-id", "R", *argv])


def test_load_documents_forwards_normalize_run_scope(monkeypatch):
    # WHY(ALPHA-1031): SFN이 넘긴 NormalizeNews run_id가 CLI에서 사라지면 manifest 소비 경로가
    #      있어도 정상 실행은 쓸 수 없고, 날짜 추측이나 풀스캔으로 퇴행한다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    captured = {}
    monkeypatch.setattr(run_mod.load_documents, "run",
                        lambda *args, **kwargs: captured.update(kwargs) or 0)
    monkeypatch.setattr(run_mod, "db_config_from_env", lambda _cfg: object())

    assert main(["load-documents", "--run-id", "R", "--input-run-id", "N1"]) == 0
    assert captured["input_run_id"] == "N1"


def test_load_documents_explicit_all_preserves_full_recovery(monkeypatch):
    # WHY(ALPHA-1031): 정상 경로를 좁혀도 운영자가 의도적으로 선택한 전체 복구는 남아야 한다.
    #      --all 자체는 스텝에 전달할 데이터가 아니라 '범위 없음=전체'를 승인하는 CLI 표식이다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    captured = {}
    monkeypatch.setattr(run_mod.load_documents, "run",
                        lambda *args, **kwargs: captured.update(kwargs) or 0)
    monkeypatch.setattr(run_mod, "db_config_from_env", lambda _cfg: object())

    assert main(["load-documents", "--run-id", "R", "--all"]) == 0
    assert captured["input_run_id"] is None
    assert captured["from_date"] is None and captured["to_date"] is None


def test_load_assertions_requires_an_explicit_scope(monkeypatch):
    """WHY(ALPHA-1033): SFN 인자 회귀가 암묵적 feature 풀스캔으로 성공하면 데이터가
    쌓일수록 정상 실행 비용이 다시 증가한다."""
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    with pytest.raises(SystemExit, match="--input-run-id, --from/--to, --all"):
        main(["load-assertions", "--run-id", "R"])


def test_load_assertions_forwards_tag_news_run_scope(monkeypatch):
    """WHY(ALPHA-1033): 현재 TagNews run_id가 CLI에서 사라지면 manifest 소비 코드가 있어도
    정상 SFN은 날짜 추측이나 전체 스캔으로 퇴행한다."""
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    captured = {}
    monkeypatch.setattr(run_mod.load_assertions, "run",
                        lambda *args, **kwargs: captured.update(kwargs) or 0)
    monkeypatch.setattr(run_mod, "db_config_from_env", lambda _cfg: object())

    assert main(["load-assertions", "--run-id", "R", "--input-run-id", "T1"]) == 0
    assert captured["input_run_id"] == "T1"


def test_load_assertions_explicit_all_preserves_full_recovery(monkeypatch):
    """WHY(ALPHA-1033): 정상 범위를 좁혀도 운영자가 의도적으로 선택한 전체 복구는
    남아야 하며, --all만 암묵적 전체 모드를 승인한다."""
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    captured = {}
    monkeypatch.setattr(run_mod.load_assertions, "run",
                        lambda *args, **kwargs: captured.update(kwargs) or 0)
    monkeypatch.setattr(run_mod, "db_config_from_env", lambda _cfg: object())

    assert main(["load-assertions", "--run-id", "R", "--all"]) == 0
    assert captured["input_run_id"] is None
    assert captured["from_date"] is None and captured["to_date"] is None


def test_load_investor_intraday_requires_an_explicit_scope(monkeypatch):
    # WHY(ALPHA-1036): SFN에서 input run이 빠졌을 때 암묵적 canonical 풀스캔으로 성공하면
    # manifest 소비 코드가 있어도 정상 운영 경로는 조용히 예전 동작으로 퇴행한다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    with pytest.raises(SystemExit, match="--input-run-id, --from/--to, --all"):
        main(["load-investor-intraday", "--run-id", "R"])


def test_load_investor_intraday_forwards_normalize_run_scope(monkeypatch):
    # WHY(ALPHA-1036): 현재 normalize run_id가 CLI에서 사라지면 loader는 manifest 직접 key와
    # winner 범위를 찾을 수 없다. 날짜 추측 없이 받은 값을 그대로 전달해야 한다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    captured = {}
    monkeypatch.setattr(run_mod.load_investor_intraday, "run",
                        lambda *args, **kwargs: captured.update(kwargs) or 0)
    monkeypatch.setattr(run_mod, "db_config_from_env", lambda _cfg: object())

    assert main([
        "load-investor-intraday", "--run-id", "R", "--input-run-id", "N1",
    ]) == 0
    assert captured["input_run_id"] == "N1"


def test_load_investor_intraday_explicit_all_preserves_full_recovery(monkeypatch):
    # WHY(ALPHA-1036): 정상 경로를 좁혀도 운영자가 의도한 전체 복구 표면은 남기되, `--all`을
    # 직접 쓴 경우만 범위 없음이 전체라는 뜻이 되어야 한다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    captured = {}
    monkeypatch.setattr(run_mod.load_investor_intraday, "run",
                        lambda *args, **kwargs: captured.update(kwargs) or 0)
    monkeypatch.setattr(run_mod, "db_config_from_env", lambda _cfg: object())

    assert main(["load-investor-intraday", "--run-id", "R", "--all"]) == 0
    assert captured["input_run_id"] is None
    assert captured["from_date"] is None and captured["to_date"] is None


@pytest.mark.parametrize("argv", [
    ["--from", "2026-08-32", "--to", "2026-08-32"],
    ["--from", "2026-08-20", "--to", "2026-08-10"],
])
def test_load_investor_intraday_rejects_invalid_recovery_window(monkeypatch, argv):
    # WHY(ALPHA-1036): 불량·역전 날짜가 문자열 필터에서 0건 성공하면 복구 완료로 오인된다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    with pytest.raises(SystemExit):
        main(["load-investor-intraday", "--run-id", "R", *argv])


@pytest.mark.parametrize("argv", [
    ["--from", "2026-08-32", "--to", "2026-08-32"],
    ["--from", "2026-08-20", "--to", "2026-08-10"],
])
def test_load_assertions_rejects_invalid_recovery_window(monkeypatch, argv):
    """WHY(ALPHA-1033): 불량·역전 날짜가 0건 성공이면 복구 완료로 오인된다."""
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    with pytest.raises(SystemExit):
        main(["load-assertions", "--run-id", "R", *argv])


@pytest.mark.parametrize("argv", [
    ["--from", "2026-08-32", "--to", "2026-08-32"],
    ["--from", "2026-08-20", "--to", "2026-08-10"],
])
def test_load_documents_rejects_invalid_recovery_window(monkeypatch, argv):
    # WHY(ALPHA-1031): 불량·역전 날짜를 문자열 필터로 처리하면 0건 성공이 되어 복구 완료처럼 보인다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    with pytest.raises(SystemExit):
        main(["load-documents", "--run-id", "R", *argv])


def test_load_etf_holdings_requires_an_explicit_scope(monkeypatch):
    # WHY(ALPHA-1011): 범위 누락이 암묵적 과거 전체 스캔으로 성공하면 SFN 배선 회귀가 데이터가
    # 쌓일수록 비싸지는 형태로 숨는다. 전체가 필요하면 운영자가 --all 을 직접 써야 한다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    with pytest.raises(SystemExit, match="--input-run-id, --from/--to, --all"):
        main(["load-etf-holdings", "--run-id", "R"])


def test_load_etf_holdings_forwards_normalize_run_scope(monkeypatch):
    # WHY(ALPHA-1011): ASL 이 넘긴 run 계보가 CLI 에서 사라지면 로더의 manifest 경로가 있어도
    # 정규 실행은 쓸 수 없다. 날짜 추측 대신 실제 normalize run_id 를 그대로 전달한다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    captured = {}
    monkeypatch.setattr(run_mod.load_etf_holdings, "run",
                        lambda *a, **kw: captured.update(kw) or 0)
    monkeypatch.setattr(run_mod, "db_config_from_env", lambda _cfg: object())

    assert main(["load-etf-holdings", "--run-id", "R", "--input-run-id", "N1"]) == 0
    assert captured["input_run_id"] == "N1"


@pytest.mark.parametrize("argv", [
    ["--from", "2026-08-32", "--to", "2026-08-32"],
    ["--from", "2026-08-20", "--to", "2026-08-10"],
])
def test_load_etf_holdings_rejects_invalid_recovery_window(monkeypatch, argv):
    # WHY(ALPHA-1011): 불량·역전 날짜를 문자열 필터로 처리하면 모든 파티션이 빠진 0건을 성공으로
    # 보고한다. 복구가 끝난 것처럼 보이지 않도록 loader 호출 전에 거부해야 한다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    with pytest.raises(SystemExit):
        main(["load-etf-holdings", "--run-id", "R", *argv])


@pytest.mark.parametrize(("step", "module", "argv"), [
    ("load-etf-holdings", "load_etf_holdings", ["load-etf-holdings", "--all"]),
    ("load-price-triggers", "load_price_triggers", ["load-price-triggers", "--all"]),
    ("normalize-news", "normalize_news", ["normalize-news"]),
    ("load-instruments", "load_instruments", ["load-instruments"]),
])
def test_canonical_holdings_consumers_get_the_universe_root_filter(monkeypatch, step, module, argv):
    """canonical holdings 를 읽는 스텝은 **전부** 유니버스 뿌리 필터를 받는다 (ALPHA-855 선행).

    이 파티션의 etf_id 집합은 분석 유니버스가 아니다 — 폐지 ETF 의 옛 행이 소급 상한만큼
    남아 있고, 참조 계열 ETF 도 곧 들어온다. 안 거르면 마스터에 없는 ETF 가 매 런
    `failed_records` 로 잡혀 `load_etf_holdings`·`load_price_triggers` 원장이 **영구
    INCOMPLETE** 가 되고, 멘션 사전과 종목 마스터에 분석 유니버스 밖 회사가 선다.

    **배선을 값으로 고정하는 이유**: 스텝 안의 필터는 인자가 안 넘어와도(기본 None)
    그대로 통과한다 — 기능이 통째로 무력화된 채 전건 초록으로 도는 형태다. 스텝 단위
    테스트가 아니라 진입점에서 잡아야 하는 종류다.
    """
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    from data_pipeline import run as run_mod

    captured = {}

    def _spy(*args, **kwargs):
        captured["expected_etfs"] = kwargs.get("expected_etfs", "인자가 아예 안 왔다")
        return 0

    monkeypatch.setattr(getattr(run_mod, module), "run", _spy)
    monkeypatch.setattr(run_mod, "db_config_from_env", lambda _cfg: object())
    assert main(argv) == 0

    settings = run_mod.load_settings(None)
    assert captured["expected_etfs"] == frozenset(settings.krx_etf.source.etf_map)
    assert captured["expected_etfs"], "필터가 비면 전량 통과라 안 건 것과 같다"
