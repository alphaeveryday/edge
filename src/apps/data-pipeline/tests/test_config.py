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


def test_changing_config_changes_targets_without_code(tmp_path):
    # WHY: 설정값 변경만으로 수집 대상이 바뀐다(코드 수정 없이).
    other = VALID.replace('symbols = ["005930"]', 'symbols = ["000660", "035720"]')
    settings = load_settings(_write(tmp_path, other))
    assert settings.targets.symbols == ["000660", "035720"]
