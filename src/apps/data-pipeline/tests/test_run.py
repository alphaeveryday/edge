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
