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
