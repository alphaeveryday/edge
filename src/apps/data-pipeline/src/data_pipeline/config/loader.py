"""TOML 베이스 파일 + 환경변수 오버라이드로 수집 설정을 로드한다.

우선순위: **환경변수 > TOML 파일**.
비밀값(api_key 등)은 커밋되는 TOML이 아니라 환경변수로만 주입한다.

    DATA_PIPELINE_NEWS__SOURCES__NAVER__API_KEY=...   # news.sources.naver.api_key 덮어쓰기

파일 경로는 인자 > 환경변수(DATA_PIPELINE_CONFIG_FILE) > 기본값(config/sources.toml) 순으로 정해진다.
이 한 줄로 환경(dev/prod)별 로딩을 구분한다.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .models import (
    CollectionTargets,
    DartFinancialConfig,
    FinancialConfig,
    KisPriceConfig,
    NewsConfig,
    PriceConfig,
    StorageConfig,
)

# 기본 설정은 패키지 안에 두고 모듈과 함께 배포한다(loader.py 옆). 이렇게 해야
# editable/wheel 어느 설치에서도 __file__ 기준으로 동일하게 찾는다.
_DEFAULT_CONFIG_FILE = Path(__file__).resolve().parent / "sources.toml"


class ConfigError(RuntimeError):
    """설정 로딩/검증 실패. 조용한 기본값 대신 항상 이 예외로 드러낸다(fail loud)."""


class Settings(BaseSettings):
    """수집 설정 루트. 수집 로직이 이 객체를 받아 쓴다."""

    model_config = SettingsConfigDict(
        env_prefix="DATA_PIPELINE_",
        env_nested_delimiter="__",
        extra="forbid",
    )

    news: NewsConfig
    price: PriceConfig
    targets: CollectionTargets
    # 재무제표는 독립 잡(스케줄 별개)이라 섹션 생략 가능 — 미설정이면 ingest-raw-financial
    # 진입점이 fail-loud 한다(뉴스·가격만 돌리는 환경은 이 섹션이 없어도 된다).
    financial: FinancialConfig | None = None
    # OpenDART(국내 재무) 도 독립 벤더다. 미설정이면 ingest-raw-financial --source dart
    # 진입점이 fail-loud 한다(FMP 재무만 돌리는 환경은 이 섹션이 없어도 된다).
    dart_financial: DartFinancialConfig | None = None
    # KIS(국내 가격) 도 독립 벤더라 섹션 생략 가능 — 미설정이면 ingest-price-raw --source kis
    # 진입점이 fail-loud 한다(FMP 만 돌리는 환경은 이 섹션이 없어도 된다).
    kis_price: KisPriceConfig | None = None
    # 스토리지는 기본 local 스텁이 있어 섹션 생략 가능(배포는 env 로 s3 지정).
    storage: StorageConfig = StorageConfig()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # 환경변수(높은 우선순위)가 파일에서 읽어 주입한 init 데이터를 덮어쓴다(env > file).
        return (env_settings, init_settings)


def load_settings(config_file: str | os.PathLike[str] | None = None) -> Settings:
    """설정을 로드해 검증된 Settings를 돌려준다. 실패는 ConfigError로 드러낸다."""
    # 경로 우선순위: 인자 > DATA_PIPELINE_CONFIG_FILE > 동봉 기본값.
    # "미지정"(None/미설정)과 "지정했으나 빈 값"을 구분한다 — 빈 값은 조용히 기본값으로
    # 넘기지 않고 fail-loud한다(예: 배포에서 env에 빈 경로가 주입된 경우).
    if config_file is not None:
        candidate, origin = os.fspath(config_file), "인자"
    elif "DATA_PIPELINE_CONFIG_FILE" in os.environ:
        candidate, origin = os.environ["DATA_PIPELINE_CONFIG_FILE"], "DATA_PIPELINE_CONFIG_FILE"
    else:
        candidate, origin = None, None

    if candidate is not None and not candidate.strip():
        raise ConfigError(
            f"설정 파일 경로({origin})가 비어 있다 — 실제 경로를 지정하거나 아예 지정하지 마라"
        )

    path = Path(candidate) if candidate is not None else _DEFAULT_CONFIG_FILE
    if not path.is_file():
        raise ConfigError(f"설정 파일을 찾을 수 없다: {path}")

    try:
        with path.open("rb") as fp:
            file_data = tomllib.load(fp)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"설정 파일 TOML 파싱 실패 ({path}):\n{exc}") from exc

    try:
        # 파일 데이터는 init으로 주입하고, 환경변수가 이를 덮어쓴다(settings_customise_sources).
        return Settings(**file_data)
    except ValueError as exc:
        # 설정 구성 실패는 공통 상위 ValueError로 잡는다: pydantic ValidationError(필드 검증
        # 실패)와 pydantic-settings SettingsError(복합 필드를 잘못된 env로 덮을 때 — 예:
        # DATA_PIPELINE_TARGETS=not-json — 소스 파싱 단계에서 발생)가 둘 다 ValueError 서브클래스다.
        # SettingsError는 버전마다 import 경로가 달라(2.2.x엔 top-level·exceptions 모두 없음)
        # 직접 import하지 않고 ValueError로 잡아 ConfigError(fail loud)로 감싼다.
        raise ConfigError(f"설정 검증 실패 ({path}):\n{exc}") from exc
