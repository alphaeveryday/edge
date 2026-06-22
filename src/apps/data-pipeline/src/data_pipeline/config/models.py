"""ALPHA-102 — 수집 설정 스키마.

뉴스/가격 데이터 소스와 수집 대상(종목/키워드)을 타입이 있는 모델로 정의한다.
필수값이 없거나 알 수 없는 키가 들어오면 pydantic이 ValidationError로 즉시 실패한다
(조용한 기본값 금지 — AGENTS Rule 12 / AC "필수 설정 누락 시 명시적 실패").
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NewsSource(BaseModel):
    """등록된 뉴스 소스 하나.

    api_key 등 비밀값은 커밋되는 파일이 아니라 환경변수로 주입한다(loader 참고).
    """

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=1)  # 필수 — 누락 시 실패
    enabled: bool = True
    api_key: str | None = None  # 비밀값: env 오버라이드 전용


class PriceSource(BaseModel):
    """가격 데이터 소스.

    ALPHA-102 범위는 가격 소스의 '설정 위치 명시'까지다. 실제 가격 수집은 후속.
    """

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=1)
    enabled: bool = True
    api_key: str | None = None


class NewsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 최소 1개 — 소스가 0개면 수집할 원천이 없다(빈 dict는 실패).
    sources: dict[str, NewsSource] = Field(min_length=1)


class PriceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: PriceSource


class CollectionTargets(BaseModel):
    """수집 대상. 설정만 바꾸면 fetcher(ALPHA-103)의 수집 대상이 바뀐다(AC4)."""

    model_config = ConfigDict(extra="forbid")

    symbols: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_at_least_one_target(self) -> CollectionTargets:
        # 종목·키워드가 모두 비면 파이프라인이 아무것도 수집하지 않고도 '성공'처럼 보인다.
        if not self.symbols and not self.keywords:
            raise ValueError(
                "targets.symbols 와 targets.keywords 가 모두 비어 있다 — 수집 대상이 없다"
            )
        return self
