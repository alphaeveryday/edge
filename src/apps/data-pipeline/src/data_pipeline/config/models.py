"""수집 설정 스키마.

뉴스/가격 데이터 소스와 수집 대상(종목/키워드)을 타입이 있는 모델로 정의한다.
필수값이 없거나 알 수 없는 키가 들어오면 pydantic이 ValidationError로 즉시 실패한다
(조용한 기본값으로 넘기지 않고 명시적으로 실패 — AGENTS Rule 12).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def _non_blank(value: str) -> str:
    """공백만 있는 문자열을 무효로 본다 — 의미 없는 값이 fail-loud를 통과하지 못하게."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("빈 문자열/공백은 허용되지 않는다")
    return stripped


# 길이만 보는 min_length=1은 "   "(공백)을 통과시킨다. strip 후 비면 실패시킨다.
NonBlankStr = Annotated[str, AfterValidator(_non_blank)]


class NewsSource(BaseModel):
    """등록된 뉴스 소스 하나.

    api_key 등 비밀값은 커밋되는 파일이 아니라 환경변수로 주입한다(loader 참고).
    """

    model_config = ConfigDict(extra="forbid")

    base_url: NonBlankStr  # 필수 — 누락·공백 불가
    enabled: bool = True
    api_key: str | None = None  # 비밀값: env 오버라이드 전용
    # our_ticker → 소스별 심볼(예: FMP 심볼). 설정으로 관리해 종목 추가에 코드 수정
    # 불필요. 매핑 없는 유니버스 종목은 이 소스가 수집하지 않는다(생략 = 제외).
    symbol_map: dict[str, NonBlankStr] = Field(default_factory=dict)


class PriceSource(BaseModel):
    """가격 데이터 소스 (FMP EOD 일봉 수집, S004).

    api_key 는 커밋되는 파일이 아니라 환경변수로 주입한다(loader 참고).
    """

    model_config = ConfigDict(extra="forbid")

    base_url: NonBlankStr
    enabled: bool = True
    api_key: str | None = None  # 비밀값: env 오버라이드 전용
    # our_ticker → FMP 심볼. 뉴스와 별개 맵이다 — 뉴스는 ADR(SSNLF·KB…)로 매핑해도
    # '그 회사 뉴스'라 맞지만, 가격은 ADR 의 USD 시세를 KR 종목 가격으로 쓰면 통화·
    # 거래시간이 어긋난다. 가격은 거래소-로컬 심볼만 두고, 없으면 이 소스가 건너뛴다.
    symbol_map: dict[str, NonBlankStr] = Field(default_factory=dict)


class KisPriceSource(BaseModel):
    """KIS(한국투자) 국내 가격 소스 (일봉 OHLCV, S004 국내).

    FMP(PriceSource)와 달리 인증이 OAuth 앱키/시크릿이고 도메인이 env(prod|vps)로 갈린다.
    그래서 base_url 대신 env 로 도메인을 고르고(경로·tr_id 는 어댑터가 고정), 비밀값
    (app_key/app_secret)은 커밋되는 파일이 아니라 환경변수로 주입한다:
        DATA_PIPELINE_KIS_PRICE__SOURCE__APP_KEY=...
        DATA_PIPELINE_KIS_PRICE__SOURCE__APP_SECRET=...
    """

    model_config = ConfigDict(extra="forbid")

    env: Literal["prod", "vps"] = "prod"
    enabled: bool = True
    app_key: str | None = None  # 비밀값: env 오버라이드 전용
    app_secret: str | None = None  # 비밀값: env 오버라이드 전용
    # our_ticker → KIS 6자리 코드. KR 은 대개 항등(005930→005930)이지만, 맵에 없는 종목
    # (US 등)은 이 소스가 건너뛴다 — KIS 는 국내(KRX) 전용이라 US 티커를 질의하면 안 된다.
    symbol_map: dict[str, NonBlankStr] = Field(default_factory=dict)


class FinancialSource(BaseModel):
    """재무제표 데이터 소스 (FMP 손익·재무상태·현금흐름 수집, S035).

    api_key 는 커밋되는 파일이 아니라 환경변수로 주입한다(loader 참고).
    base_url 은 /stable 베이스만 둔다 — 3개 엔드포인트(income-statement·balance-
    sheet-statement·cash-flow-statement)는 어댑터가 붙인다.
    """

    model_config = ConfigDict(extra="forbid")

    base_url: NonBlankStr
    enabled: bool = True
    api_key: str | None = None  # 비밀값: env 오버라이드 전용
    # our_ticker → FMP 심볼. 가격과 같은 정책 — 재무제표는 US 거래소-로컬 심볼만 둔다.
    # KR 은 FMP 재무 커버리지가 약해 후속(DART 등)으로 미룬다(없으면 이 소스가 건너뜀).
    symbol_map: dict[str, NonBlankStr] = Field(default_factory=dict)


class DartFinancialSource(BaseModel):
    """OpenDART 국내 재무제표 소스 (financial_statements raw).

    api_key 는 커밋되는 파일이 아니라 환경변수로 주입한다:
        DATA_PIPELINE_DART_FINANCIAL__SOURCE__API_KEY=...
    """

    model_config = ConfigDict(extra="forbid")

    base_url: NonBlankStr = "https://opendart.fss.or.kr/api"
    enabled: bool = True
    api_key: str | None = None  # 비밀값: env 오버라이드 전용
    # our_ticker → KRX 6자리 종목코드. corp_code 는 OpenDART corpCode.xml 로 런타임 매핑한다.
    symbol_map: dict[str, NonBlankStr] = Field(default_factory=dict)
    # 비우면 어댑터가 KST 기준 현재연도와 직전연도를 조회한다.
    years: list[NonBlankStr] = Field(default_factory=list)
    # 11011=사업, 11012=반기, 11013=1분기, 11014=3분기.
    reprt_codes: list[NonBlankStr] = Field(
        default_factory=lambda: ["11011", "11012", "11013", "11014"]
    )


class NewsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 최소 1개 — 소스가 0개면 수집할 원천이 없다(빈 dict는 실패).
    sources: dict[str, NewsSource] = Field(min_length=1)


class PriceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: PriceSource


class KisPriceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: KisPriceSource


class FinancialConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: FinancialSource


class DartFinancialConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: DartFinancialSource


class StorageConfig(BaseModel):
    """레이크 스토리지 백엔드 선택.

    MVP 개발은 local(스텁)로 돌리고, 배포는 env 로 s3 + bucket 을 주입한다:
        DATA_PIPELINE_STORAGE__BACKEND=s3
        DATA_PIPELINE_STORAGE__BUCKET=stock-ai-lake
    """

    model_config = ConfigDict(extra="forbid")

    backend: Literal["local", "s3"] = "local"
    local_root: NonBlankStr = ".lake"  # local 백엔드의 루트 디렉터리
    bucket: str | None = None  # s3 백엔드 필수

    @model_validator(mode="after")
    def _s3_requires_bucket(self) -> StorageConfig:
        # bucket 없이 s3 로 부팅하면 첫 put 에서야 죽는다 — 로드 시점에 fail loud.
        if self.backend == "s3" and not (self.bucket or "").strip():
            raise ValueError("storage.backend=s3 인데 storage.bucket 이 없다")
        return self


class CollectionTargets(BaseModel):
    """수집 대상. 설정만 바꾸면 fetcher의 수집 대상이 바뀐다(코드 수정 없이)."""

    model_config = ConfigDict(extra="forbid")

    symbols: list[NonBlankStr] = Field(default_factory=list)
    keywords: list[NonBlankStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_at_least_one_target(self) -> CollectionTargets:
        # 종목·키워드가 모두 비면 파이프라인이 아무것도 수집하지 않고도 '성공'처럼 보인다.
        if not self.symbols and not self.keywords:
            raise ValueError(
                "targets.symbols 와 targets.keywords 가 모두 비어 있다 — 수집 대상이 없다"
            )
        return self
