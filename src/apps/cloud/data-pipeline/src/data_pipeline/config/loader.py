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

from pydantic import model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .models import (
    DbConfig,
    BigKindsNewsSource,
    CollectionTargets,
    DartDisclosureConfig,
    DartFinancialConfig,
    EtfConfig,
    FinancialConfig,
    KisInvestorConfig,
    KisNavConfig,
    KisPriceConfig,
    MinuteConsumerConfig,
    MinuteDisclosureWorkerConfig,
    MinuteNewsConsumerConfig,
    MinuteNewsWorkerConfig,
    MinutePriceConsumerConfig,
    MinutePriceWorkerConfig,
    MinuteRelayConfig,
    MinuteSectorIndexConfig,
    MinuteUniverseConfig,
    KrxEtfConfig,
    KrxInstrumentConfig,
    NewsConfig,
    PriceConfig,
    PriceTriggersConfig,
    StorageConfig,
    YahooPriceConfig,
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
    # ETF 구성종목(FMP holdings, US) 은 독립 잡이라 섹션 생략 가능 — 미설정이면
    # ingest-raw-etf 진입점이 fail-loud 한다(ETF 를 안 돌리는 환경은 생략 가능).
    etf: EtfConfig | None = None
    # KRX(국내 ETF 구성종목) 도 독립 벤더라 섹션 생략 가능 — 미설정이면 ingest-raw-etf
    # --source krx 진입점이 fail-loud 한다(US ETF 만 돌리는 환경은 생략 가능).
    krx_etf: KrxEtfConfig | None = None
    # KRX 공식 OpenAPI 종목기본정보(ALPHA-829) — krx_etf 와 **자격증명·호스트가 모두 다른**
    # 별개 벤더라 섹션도 따로다. 미설정이면 ingest-raw-instrument 진입점이 fail-loud 한다.
    krx_instrument: KrxInstrumentConfig | None = None
    # OpenDART 공시(disclosure) 는 재무와 별개 잡·별개 API(list.json/document.xml)다. 미설정이면
    # ingest-raw-disclosure 진입점이 fail-loud 한다(공시를 안 돌리는 환경은 생략 가능).
    dart_disclosure: DartDisclosureConfig | None = None
    # BigKinds(국내 뉴스)는 news.sources dict 밖의 독립 벤더다. 미설정이면 ingest-raw
    # --source bigkinds 진입점이 fail-loud 한다(FMP 뉴스만 돌리는 환경은 생략 가능).
    bigkinds_news: BigKindsNewsSource | None = None
    # KIS(국내 가격) 도 독립 벤더라 섹션 생략 가능 — 미설정이면 ingest-price-raw --source kis
    # 진입점이 fail-loud 한다(FMP 만 돌리는 환경은 이 섹션이 없어도 된다).
    kis_price: KisPriceConfig | None = None
    # Yahoo(yfinance) 가격 — **지수 시계열 전용** 보강 소스. 인증이 없어 크리덴셜 주입이
    # 필요 없고, 섹션이 없으면 `ingest-price-raw --source yahoo` 가 fail-loud 한다.
    yahoo_price: YahooPriceConfig | None = None
    # KIS ETF NAV(ALPHA-380) 도 독립 잡이라 섹션 생략 가능 — 미설정이면 ingest-raw-nav
    # 진입점이 fail-loud 한다. 수집 유니버스는 krx_etf.source.etf_map 을 공유한다.
    kis_nav: KisNavConfig | None = None
    # KIS 종목별 투자자 수급(ALPHA-482) 도 독립 잡이라 섹션 생략 가능 — 미설정이면
    # ingest-raw-investor 진입점이 fail-loud 한다. 수집 유니버스는 canonical KR holdings 에서
    # 파생한다(가격과 같은 축, universe_from_holdings).
    kis_investor: KisInvestorConfig | None = None
    # DB(Cloud Event Store)는 적재 스텝(load-*)만 쓴다 — 수집·정제만 돌리는 환경은 생략
    # 가능하고, 미설정이면 load-* 진입점이 fail-loud 한다.
    db: DbConfig | None = None
    # ETF 가격변동 트리거(ALPHA-406)는 load-price-triggers 만 쓴다 — 미설정이면 그 진입점이
    # fail-loud 한다(트리거를 안 돌리는 환경은 생략 가능).
    price_triggers: PriceTriggersConfig | None = None
    # 1분 Outbox Relay(ALPHA-670)는 `relay` 스텝만 쓴다 — 미설정이면 그 진입점이
    # fail-loud 한다(Relay 를 안 돌리는 환경은 생략 가능).
    minute_relay: MinuteRelayConfig | None = None
    # 1분 Consumer 운영 설정(ALPHA-672)은 `dlq-reconcile` 만 쓴다 — 미설정이면 그
    # 진입점이 fail-loud 한다.
    minute_consumer: MinuteConsumerConfig | None = None
    # 1분 Price Worker(ALPHA-706)는 `price-worker` 스텝만 쓴다 — 미설정이면 그
    # 진입점이 fail-loud 한다(토스 자격증명은 env 로만).
    minute_price_worker: MinutePriceWorkerConfig | None = None
    # 1분 가격 판정 Consumer(ALPHA-711)는 `price-consumer` 스텝만 쓴다 — 미설정이면
    # 그 진입점이 fail-loud 한다.
    minute_price_consumer: MinutePriceConsumerConfig | None = None
    # 1분 뉴스 추출 Consumer(ALPHA-713)는 `news-consumer` 스텝만 쓴다 — 미설정이면
    # 그 진입점이 fail-loud 한다.
    minute_news_consumer: MinuteNewsConsumerConfig | None = None
    # 1분 universe.json **생성** 입력(build_minute_universe). 수집 스텝은 아무도 안 읽는다
    # — 1분 레인의 유니버스 정본은 S3 객체지 이 섹션이 아니다. 미설정이면 섹터 후보
    # 없이(=holdings 파생 ETF 만) universe 를 만든다.
    minute_universe: MinuteUniverseConfig | None = None
    # 1분 업종지수 dataset(ALPHA-887)의 **수집 대상 정본**. universe.json 과 달리 이건
    # config 가 곧 정본이다(이 dataset 은 `UNIVERSE_DATASETS` 밖이다).
    # ⏭ 지금은 읽는 코드가 없다 — 어댑터(`sources/kis_sector_index.py`)만 있고 레인
    # 배선은 다음 PR 이다. 그 진입점이 미설정을 fail-loud 로 거부해야 한다(기대 집합 0
    # 으로 도는 것보다 낫다). `KisSectorIndexClient` 는 빈 맵을 이미 기동에서 막는다.
    minute_sector_index: MinuteSectorIndexConfig | None = None
    # 1분 News Worker(ALPHA-707)는 `news-worker` 스텝만 쓴다. 기본값이 전부라 섹션이
    # 없어도 기동한다 — 엔드포인트 정본은 [bigkinds_news] 라 이 섹션은 수치뿐이다.
    minute_news_worker: MinuteNewsWorkerConfig = MinuteNewsWorkerConfig()
    # 1분 Disclosure Worker(ALPHA-875)는 `disclosure-worker` 스텝만 쓴다. 뉴스와 같이
    # 기본값이 전부라 섹션이 없어도 기동한다 — 엔드포인트·유형 필터 정본은
    # [dart_disclosure.source] 라 이 섹션은 pacing·예산 수치뿐이다.
    minute_disclosure_worker: MinuteDisclosureWorkerConfig = MinuteDisclosureWorkerConfig()
    # 스토리지는 기본 local 스텁이 있어 섹션 생략 가능(배포는 env 로 s3 지정).
    storage: StorageConfig = StorageConfig()

    @model_validator(mode="after")
    def _validate_reference_etfs_have_holdings(self) -> Settings:
        """섹터 후보로 선언한 ETF 는 전부 KRX 명부 대상이어야 한다 (ALPHA-855).

        같은 48종이 두 섹션에 적힌다 — `[minute_universe].sector_etf_ids` 는 "분봉을 받아라",
        `[krx_etf.source.reference_etf_map]` 은 "명부를 받아라". 한 ETF 를 앞쪽에만 적었을 때
        **틀리는 방향이 나쁘다**: 명부가 없으면 `layers.holdings` 가 FMP 폴백으로 내려가고
        거기에도 없으면 빈 목록이라, 겹침 게이트 `overlap()` 이 **0.0** 을 낸다. 0.0 은
        "동어반복이 아니다"라는 뜻이라 그 ETF 가 섹터층 후보로 살아남는다 — 실제로는 같은
        포트폴리오일 수 있는데도. 즉 결손이 **관대한 쪽 오답**으로 나타나고, 사유를 남기는
        자리(twins·alien·rho_blocked)는 들어온 후보만 기록하므로 사유 없는 오답이다.
        그래서 첫 사용처가 아니라 **로드 시점**에 죽인다.

        반대 방향(reference_etf_map 에만 있는 ETF)은 안 막는다 — 안 쓰는 명부를 받는 수집
        낭비일 뿐 오답을 만들지 않는다. 안 깨진 것에 가드를 걸지 않는다(Rule 2).

        ⚠️ 대조군은 `reference_etf_map` **하나**다. `etf_map` 까지 인정하면 섹터 후보를 거기
        적어도 통과하는데, 그건 이 축을 가른 이유 자체(구성종목이 1분 유니버스로 딸려 들어와
        410 → 1,400 unit)를 되돌리는 설정이다. `build_minute_universe` 가 뒤늦게 거부하긴
        하지만, 거부를 로드 시점으로 당기고 **어느 맵에 넣어야 하는지**까지 말해 주는 편이
        낫다 — 가드가 옳은 자리를 안내하지 않으면 사람은 통과하는 자리를 찾는다.
        """
        if self.minute_universe is None or self.krx_etf is None:
            return self  # 한쪽이 없으면 대조할 짝이 없다(둘 다 선택 섹션이다)
        reference = set(self.krx_etf.source.reference_etf_map)
        if missing := sorted(set(self.minute_universe.sector_etf_ids) - reference):
            in_root = sorted(set(missing) & set(self.krx_etf.source.etf_map))
            hint = (
                f" (그중 {in_root} 는 etf_map 에 있다 — 거기 두면 구성종목이 1분 유니버스로 "
                f"딸려 들어와 수집이 410 → 1,400 unit 로 뛴다)" if in_root else ""
            )
            raise ValueError(
                f"[minute_universe].sector_etf_ids 에 있는데 참조 계열 명부 대상이 아닌 ETF: "
                f"{missing} — [krx_etf.source.reference_etf_map] 에 ISIN 과 함께 더해라"
                f"{hint}. 명부가 없으면 겹침 게이트가 0.0 을 내어 동어반복 ETF 가 섹터층으로 "
                f"뽑힌다"
            )
        return self

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
