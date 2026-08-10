"""수집 설정 로더 테스트.

각 테스트는 '왜 이 동작이 중요한가'를 주석으로 남긴다 — AGENTS Rule 9.
설정 로딩은 결정론적 변환이라 코드로만 검증한다(Rule 5).
"""

from pathlib import Path

import pytest

from data_pipeline import ConfigError, load_settings

VALID = """
[news.sources.naver]
base_url = "https://example.com/news"

[price.source]
base_url = "https://example.com/price"

[targets]
symbols = ["005930"]
keywords = ["금리"]
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "sources.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_typed_settings(tmp_path):
    # 정상 파일은 타입이 있는 Settings로 로드된다(설정값으로 관리).
    settings = load_settings(_write(tmp_path, VALID))

    assert settings.news.sources["naver"].base_url == "https://example.com/news"
    assert settings.price.source.base_url == "https://example.com/price"
    assert settings.targets.symbols == ["005930"]
    assert settings.targets.keywords == ["금리"]


def test_missing_required_base_url_fails_loud(tmp_path):
    # WHY: base_url 누락을 조용히 기본값으로 넘기면 fetcher가 빈/잘못된 소스로 수집한다.
    bad = VALID.replace('base_url = "https://example.com/news"', "")
    with pytest.raises(ConfigError):
        load_settings(_write(tmp_path, bad))


def test_no_news_source_fails_loud(tmp_path):
    # WHY: 뉴스 소스가 0개면 수집할 원천이 없다 — 명시적 실패여야 한다.
    bad = """
[price.source]
base_url = "https://example.com/price"

[targets]
symbols = ["005930"]
"""
    with pytest.raises(ConfigError):
        load_settings(_write(tmp_path, bad))


def test_empty_targets_fails_loud(tmp_path):
    # WHY: 종목·키워드가 모두 비면 파이프라인이 아무것도 수집하지 않고도 '성공'처럼 보인다.
    bad = """
[news.sources.naver]
base_url = "https://example.com/news"

[price.source]
base_url = "https://example.com/price"

[targets]
symbols = []
keywords = []
"""
    with pytest.raises(ConfigError):
        load_settings(_write(tmp_path, bad))


def test_whitespace_base_url_fails_loud(tmp_path):
    # WHY: 공백만 있는 base_url은 길이는 있지만 무효 — 통과하면 fetcher가 빈 URL을 질의한다.
    bad = VALID.replace('base_url = "https://example.com/news"', 'base_url = "   "')
    with pytest.raises(ConfigError):
        load_settings(_write(tmp_path, bad))


def test_blank_target_entry_fails_loud(tmp_path):
    # WHY: symbols=[""] 처럼 빈 대상이 끼면 '대상 있음'으로 통과하나 실제론 무의미 — 명시적 실패.
    bad = VALID.replace('symbols = ["005930"]', 'symbols = [""]')
    with pytest.raises(ConfigError):
        load_settings(_write(tmp_path, bad))


def test_unknown_key_rejected(tmp_path):
    # WHY: 오타 난 설정 키를 조용히 무시하면 의도한 설정이 적용되지 않은 채 돈다(extra=forbid).
    bad = """
[news.sources.naver]
base_url = "https://example.com/news"
oops = "typo"

[price.source]
base_url = "https://example.com/price"

[targets]
symbols = ["005930"]
"""
    with pytest.raises(ConfigError):
        load_settings(_write(tmp_path, bad))


def test_missing_config_file_fails_loud(tmp_path):
    # WHY: 파일이 없는데 기본값으로 조용히 부팅하면 안 된다 — fail loud(Rule 12).
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "does-not-exist.toml")


def test_env_fills_secret_absent_from_file(tmp_path, monkeypatch):
    # WHY: 비밀값(api_key)은 커밋되는 파일에 없고 env로 주입된다.
    monkeypatch.setenv(
        "DATA_PIPELINE_NEWS__SOURCES__NAVER__API_KEY", "secret-from-env"
    )
    settings = load_settings(_write(tmp_path, VALID))
    assert settings.news.sources["naver"].api_key == "secret-from-env"


def test_env_overrides_value_present_in_file(tmp_path, monkeypatch):
    # WHY: env > file 우선순위. 파일에 이미 있는 값도 env가 덮어쓴다(채우기만이 아니라 오버라이드).
    monkeypatch.setenv(
        "DATA_PIPELINE_NEWS__SOURCES__NAVER__BASE_URL", "https://override.example.com"
    )
    settings = load_settings(_write(tmp_path, VALID))
    assert settings.news.sources["naver"].base_url == "https://override.example.com"


def test_blank_config_arg_fails_loud(tmp_path):
    # WHY: load_settings("")는 '명시했으나 빈 값' — 조용히 기본값으로 폴백하면 안 되고 실패해야 한다.
    with pytest.raises(ConfigError):
        load_settings("")
    with pytest.raises(ConfigError):
        load_settings("   ")


def test_blank_config_env_fails_loud(monkeypatch):
    # WHY: 배포에서 DATA_PIPELINE_CONFIG_FILE에 빈 값이 주입되면 placeholder 기본값으로
    #      조용히 부팅하는 대신 fail-loud해야 한다(문서화된 경로 우선순위).
    monkeypatch.setenv("DATA_PIPELINE_CONFIG_FILE", "")
    with pytest.raises(ConfigError):
        load_settings()


def test_malformed_env_override_fails_loud(tmp_path, monkeypatch):
    # WHY: 복합 필드를 잘못된 env로 덮으면(DATA_PIPELINE_TARGETS=not-json) pydantic-settings는
    #      ValidationError가 아니라 SettingsError를 던진다. ConfigError로 감싸지 않으면
    #      load_settings의 fail-loud 계약(실패=ConfigError)이 깨져 호출부가 못 잡는다.
    monkeypatch.setenv("DATA_PIPELINE_TARGETS", "not-json")
    with pytest.raises(ConfigError):
        load_settings(_write(tmp_path, VALID))


def test_default_config_loads_without_args(monkeypatch):
    # WHY: 인자·env 없이도 패키지 동봉 기본 설정이 로드돼야 한다. 기본 경로 해석이
    #      깨지면(예: wheel 설치) load_settings()가 ConfigError로 죽는다.
    monkeypatch.delenv("DATA_PIPELINE_CONFIG_FILE", raising=False)
    settings = load_settings()
    assert settings.news.sources  # 비어 있지 않음
    assert settings.targets.symbols or settings.targets.keywords


def test_changing_config_changes_targets_without_code(tmp_path):
    # WHY: 설정값 변경만으로 수집 대상이 바뀐다(코드 수정 없이).
    other = VALID.replace('symbols = ["005930"]', 'symbols = ["000660", "035720"]')
    settings = load_settings(_write(tmp_path, other))
    assert settings.targets.symbols == ["000660", "035720"]


def test_financial_section_optional(tmp_path):
    # WHY: 재무제표는 독립 잡이라 섹션이 없어도 로드돼야 한다(뉴스·가격만 돌리는 환경).
    #      진입점(ingest-raw-financial)이 None 을 fail-loud 로 잡는다.
    settings = load_settings(_write(tmp_path, VALID))
    assert settings.financial is None


def test_financial_section_parsed_when_present(tmp_path):
    # WHY: [financial.source] 가 있으면 타입 있는 설정으로 로드돼 재무 잡이 쓴다.
    text = VALID + """
[financial.source]
base_url = "https://example.com/stable"

[financial.source.symbol_map]
NVDA = "NVDA"
"""
    settings = load_settings(_write(tmp_path, text))
    assert settings.financial.source.base_url == "https://example.com/stable"
    assert settings.financial.source.symbol_map == {"NVDA": "NVDA"}


def test_dart_financial_section_optional(tmp_path):
    # WHY: OpenDART 재무는 독립 벤더라 섹션이 없어도 기존 FMP 재무/뉴스/가격 환경은
    #      그대로 로드돼야 한다. --source dart 진입점이 None 을 fail-loud 로 잡는다.
    settings = load_settings(_write(tmp_path, VALID))
    assert settings.dart_financial is None


def test_dart_financial_section_parsed_when_present(tmp_path):
    # WHY: [dart_financial.source] 가 있으면 타입 있는 설정으로 로드돼 DART 재무 잡이 쓴다.
    #      api_key 는 파일이 아니라 env 로 주입되며, 종목 맵은 KR 6자리 코드를 담는다.
    text = VALID + """
[dart_financial.source]
base_url = "https://opendart.example/api"
years = ["2025"]
reprt_codes = ["11011"]

[dart_financial.source.symbol_map]
"005930" = "005930"
"""
    settings = load_settings(_write(tmp_path, text))
    assert settings.dart_financial.source.base_url == "https://opendart.example/api"
    assert settings.dart_financial.source.years == ["2025"]
    assert settings.dart_financial.source.reprt_codes == ["11011"]
    assert settings.dart_financial.source.symbol_map == {"005930": "005930"}


def test_bigkinds_news_section_optional(tmp_path):
    # WHY: BigKinds 는 독립 뉴스 벤더라 섹션이 없어도 기존 FMP 뉴스 환경은 그대로 로드돼야 한다.
    #      --source bigkinds 진입점이 None 을 fail-loud 로 잡는다.
    settings = load_settings(_write(tmp_path, VALID))
    assert settings.bigkinds_news is None


def test_bigkinds_news_section_parsed_when_present(tmp_path):
    # WHY: [bigkinds_news] 가 있으면 타입 있는 설정으로 로드돼 BigKinds 뉴스 잡이 쓴다.
    #      수집 범위(카테고리)는 코드가 아니라 설정으로 관리한다(ALPHA-417).
    text = VALID + """
[bigkinds_news]
base_url = "https://bigkinds.example/search.do"
page_size = 25
max_pages = 2
category_codes = ["002000000"]
"""
    settings = load_settings(_write(tmp_path, text))
    assert settings.bigkinds_news.base_url == "https://bigkinds.example/search.do"
    assert settings.bigkinds_news.page_size == 25
    assert settings.bigkinds_news.max_pages == 2
    assert settings.bigkinds_news.category_codes == ["002000000"]


def test_krx_etf_isins_match_their_short_codes():
    # WHY: etf_map 의 ISIN 은 손으로 적는 값이고, 오타가 나도 형식은 멀쩡해 보인다. 틀린 ISIN 은
    #      KRX 가 빈 output 을 주고 그 ETF 가 런 단위로 실패한다(krx_etf.py — partial 로 드러남).
    #      31종까지 늘어난 지금(ALPHA-454) 그건 매일 시끄러운 실패라, 오타를 런타임이 아니라
    #      여기서 잡는다. 체크디짓까지 봐야 한 자리 오타가 걸린다 — 접두사만 보면 KR7091*1*60002
    #      같은 실수가 통과한다.
    settings = load_settings()

    for short_code, isin in settings.krx_etf.source.etf_map.items():
        body, expected = isin[:-1], isin[-1]
        assert body == f"KR7{short_code}00", f"{short_code}: ISIN 본문 불일치 ({isin})"
        digits = "".join(str(int(c, 36)) if c.isalpha() else c for c in body)
        total, double = 0, True
        for c in reversed(digits):
            d = int(c) * 2 if double else int(c)
            total += d - 9 if d > 9 else d
            double = not double
        assert str((10 - total % 10) % 10) == expected, f"{short_code}: 체크디짓 불일치 ({isin})"


def test_minute_relay_queue_urls_from_documented_env_form(monkeypatch, tmp_path):
    """README·docstring 이 안내하는 env 형태가 **실제로 파싱되는지** 고정한다.

    처음 문서에 적었던 nested 형태(`…__QUEUE_URLS__price-analysis-realtime=`)는 셸이
    변수 할당으로 파싱하지 못해(destination 이름에 하이픈) 그 명령 자체가 실행되지
    않았다 — 새 실행 표면의 첫 문서 경로가 죽어 있었다(봇 리뷰 P2). 문서가 안내하는
    형태와 코드가 받는 형태는 테스트로 묶어 둔다.
    """
    monkeypatch.setenv("DATA_PIPELINE_DB__PASSWORD", "x")
    monkeypatch.setenv(
        "DATA_PIPELINE_MINUTE_RELAY__QUEUE_URLS",
        '{"price-analysis-realtime":"https://sqs/p",'
        '"news-extraction-realtime":"https://sqs/n",'
        '"news-extraction-backfill":"https://sqs/b",'
        '"price-explanation-realtime":"https://sqs/e"}',
    )
    settings = load_settings(_write(tmp_path, VALID))
    assert dict(settings.minute_relay.queue_urls) == {
        "price-analysis-realtime": "https://sqs/p",
        "news-extraction-realtime": "https://sqs/n",
        "news-extraction-backfill": "https://sqs/b",
        # 4번째 — 트리거 설명 큐(ALPHA-709). 빠뜨리면 RelayConfig 가 기동을 거부한다
        "price-explanation-realtime": "https://sqs/e",
    }
    # 이 매핑이 그대로 Relay 설정으로 성립해야 한다(어휘·중복 검증 통과)
    from data_pipeline.minute.relay import RelayConfig

    RelayConfig(relay_id="r", queue_urls=dict(settings.minute_relay.queue_urls))


@pytest.mark.parametrize("value", ["ABCDEF", "가나다라마바", "09117", "09 170"])
def test_sector_etf_bad_shape_fails_loud(tmp_path, value):
    # WHY: 형태가 아닌 값이 통과하면 universe.json 에 실려 매분 missing 으로 잡히고 그
    #      window 가 영구 INCOMPLETE 로 남는다. 판정은 `krx_short_code` 하나로 간다.
    #      **길이 검사로 대신할 수 없다** — 앞의 두 값은 정확히 6자라 길이는 통과하고,
    #      그러면 6자 US 심볼과 한글이 KIS(국내 전용)로 질의된다. 그 둘이 이 목록에
    #      있는 이유가 그것이다(뒤의 둘은 길이·공백 축).
    #      (자릿수를 바꿔 적은 오타는 여기서 못 잡는다 — 형태는 맞기 때문이다.
    #       그건 첫 런 manifest 의 missing 으로 드러나고, 그 한계는 모델 주석에 있다.)
    bad = VALID + f"""
[minute_universe]
sector_etf_ids = ["091170", "{value}"]
"""
    with pytest.raises(ConfigError):
        load_settings(_write(tmp_path, bad))


def test_sector_etf_duplicate_fails_loud(tmp_path):
    # WHY: 여기가 **중복이 드러나는 유일한 자리**다 — 빌더는 집합 연산이라
    #      (`sorted(set(sector_etf_ids))`) 중복을 조용히 삼키고 `Universe` 는 애초에
    #      한 벌만 본다. 중복 한 줄은 대개 "다른 코드를 적으려다 덮어썼다"의 흔적이라,
    #      삼켜지면 의도한 종목 하나가 아무 신호 없이 유니버스에서 사라진다.
    bad = VALID + """
[minute_universe]
sector_etf_ids = ["091170", "091170"]
"""
    with pytest.raises(ConfigError):
        load_settings(_write(tmp_path, bad))


def test_sector_etf_unknown_key_fails_loud(tmp_path):
    # WHY: `extra="forbid"` 가 없으면 키 오타(sector_etf_id)가 조용히 로드되고
    #      sector_etf_ids 는 빈 튜플이 된다 — 47종이 통째로 사라진 채 초록으로 돈다.
    #      값 오타는 위에서 막는데 키 오타를 안 막으면 같은 결과에 신호만 없다.
    bad = VALID + """
[minute_universe]
sector_etf_id = ["091170"]
"""
    with pytest.raises(ConfigError):
        load_settings(_write(tmp_path, bad))


def test_sector_etf_list_loads(tmp_path):
    # WHY: 이 섹션은 1분 universe.json **생성** 입력이다 — 수집 스텝은 안 읽는다. 값이
    #      튜플로 그대로 서야 build 스크립트가 holdings 파생 ETF 와 합집합할 수 있다.
    good = VALID + """
[minute_universe]
sector_etf_ids = ["091170", "0093A0"]
"""
    settings = load_settings(_write(tmp_path, good))
    assert settings.minute_universe.sector_etf_ids == ("091170", "0093A0")


def test_minute_universe_section_is_optional(tmp_path):
    # WHY: 섹터 후보 없이도 1분 레인은 돌아야 한다(이 축이 생기기 전과 같은 유니버스).
    #      필수로 만들면 이 섹션이 없는 환경의 로드가 통째로 죽는다.
    assert load_settings(_write(tmp_path, VALID)).minute_universe is None
