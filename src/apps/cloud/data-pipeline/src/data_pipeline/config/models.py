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


def _bigkinds_category_code(value: str) -> str:
    """BigKinds 카테고리 코드는 9자리 숫자다(`002000000` = 경제 대분류).

    형식을 여기서 막는 이유: **BigKinds 는 잘못된 코드에 에러를 안 준다 — HTTP 200 에
    빈 resultList 를 준다**(라이브 실측: `002`·`999000000`·`abc` 전부 totalCount=0).
    그러면 전 심볼이 0행이 되는데 수집 스텝은 실패도 매핑누락도 아니라 **success 로**
    기록한다(ingest_raw 의 상태 판정은 real_failures 나 planned_symbols==0 만 본다).
    오타 하나가 뉴스 수집을 통째로 죽이면서 파이프라인은 초록불인 상태가 된다.

    그래서 오타류는 **로드 시점에** 터뜨린다(DbConfig 비밀번호 검사와 같은 결).
    9자리 숫자지만 실재하지 않는 코드(`999000000`)는 여기서 못 잡는다 — 그건 형식이
    아니라 의미라 라이브 조회 없이는 알 수 없다.
    """
    stripped = value.strip()
    if len(stripped) != 9 or not stripped.isdigit():
        raise ValueError(
            f"BigKinds 카테고리 코드는 9자리 숫자여야 한다(예: 002000000) — 받은 값: {value!r}"
        )
    return stripped


BigKindsCategoryCode = Annotated[str, AfterValidator(_bigkinds_category_code)]


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


class BigKindsNewsSource(BaseModel):
    """BigKinds 국내 뉴스 소스 (stock_news raw) — 카테고리 주도 전체 수집(ALPHA-417).

    BigKinds search.do 는 키 없이 호출하지만, 저부하를 위해 page_size/max_pages 를 설정으로 둔다.
    검색어(query_map)는 없다 — 종목 매핑은 정규화의 종목명 탐지(ALPHA-416) 소관이다.
    """

    model_config = ConfigDict(extra="forbid")

    base_url: NonBlankStr = "https://www.bigkinds.or.kr/api/news/search.do"
    enabled: bool = True
    page_size: int = Field(default=100, ge=1, le=100)
    max_pages: int = Field(default=40, ge=1, le=100)
    # 수집 범위를 정하는 BigKinds 카테고리 대분류 코드 — **필수(최소 1개)**. 검색어가 없으므로
    # 카테고리마저 비면 전체 뉴스 firehose 다 — 로드 시점에 거부한다(fail loud). 우리 소비자
    # (태깅)는 경제 사건만 쓴다.
    category_codes: list[BigKindsCategoryCode] = Field(min_length=1)


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


class YahooPriceSourceConfig(BaseModel):
    """Yahoo(yfinance) 가격 소스 — **벤치마크 지수 시계열** 보강 (KR 전용).

    인증이 없어 비밀값 필드도 없다. KR 개별주·ETF 자체 종가는 KIS 가 이미 커버하므로
    (`ingest_price_raw` 가 holdings 에서 ETF 자신도 유니버스에 넣는다, ALPHA-419)
    이 소스의 존재 이유는 `market_series` 를 채울 **지수**다 — 지수 시계열이 없어
    L0 상대 게이트가 미적용이고 시장 성분 제거를 횡단면 평균으로 대신하고 있다.

    `index_map` 은 targets/holdings 와 무관하게 **항상** 수집한다(지수는 우리 유니버스의
    종목이 아니라 대조축이라 symbols 로 들어올 길이 없다).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    # our_key → Yahoo 심볼. 지수는 접미사 규칙이 없어 명시 맵으로만 둔다.
    index_map: dict[str, NonBlankStr] = Field(default_factory=dict)
    # our_ticker → Yahoo 심볼 오버라이드. KOSDAQ(.KQ) 처럼 접미사 규칙에서 벗어나는 것만.
    symbol_map: dict[str, NonBlankStr] = Field(default_factory=dict)
    # 맵에 없는 KR 단축코드에 붙일 기본 접미사. KOSPI=.KS 가 기본 — 추정이 위험한
    # KOSDAQ 은 symbol_map 으로 명시한다(조용히 다른 시장 종목을 붙이지 않도록).
    suffix: str = ".KS"



class KisNavSource(BaseModel):
    """KIS(한국투자) 국내 ETF NAV 소스 — nav-comparison-daily-trend, tr_id FHPST02440200 (ALPHA-380).

    인증 축은 KisPriceSource 와 같다(OAuth 앱키/시크릿·env 도메인). 비밀값은 env 로만 주입:
        DATA_PIPELINE_KIS_NAV__SOURCE__APP_KEY=...
        DATA_PIPELINE_KIS_NAV__SOURCE__APP_SECRET=...

    **etf_map 이 여기 없는 건 의도다** — 수집 유니버스는 `krx_etf.source.etf_map`(ALPHA-454
    국내 반도체 30 + KODEX 200)을 그대로 쓴다. 같은 ETF 목록을 두 섹션에 복제하면 한쪽만
    갱신돼 구성종목과 NAV 의 유니버스가 어긋난다. KIS 는 ISIN 이 아니라 6자리 단축코드로
    질의하므로 표준코드에서 파생한다(krx_etf._short_code).
    """

    model_config = ConfigDict(extra="forbid")

    env: Literal["prod", "vps"] = "prod"
    enabled: bool = True
    app_key: str | None = None  # 비밀값: env 오버라이드 전용
    app_secret: str | None = None  # 비밀값: env 오버라이드 전용


class KisInvestorSource(BaseModel):
    """KIS(한국투자) 국내 종목별 투자자 수급 소스 — investor-trade-by-stock-daily, tr_id FHPTJ04160001 (ALPHA-482).

    인증 축은 KisPriceSource 와 같다(OAuth 앱키/시크릿·env 도메인). 비밀값은 env 로만 주입:
        DATA_PIPELINE_KIS_INVESTOR__SOURCE__APP_KEY=...
        DATA_PIPELINE_KIS_INVESTOR__SOURCE__APP_SECRET=...

    수집 유니버스는 가격(KisPriceSource)과 같은 축이다 — canonical KR holdings 최신 스냅샷의
    구성종목(개별주식)에서 파생한다(universe_from_holdings, ALPHA-419). ETF 자체가 아니라 편입
    종목의 수급을 모아 ETF 움직임을 설명한다. symbol_map 은 항등이 아닌 예외의 오버라이드 축.
    """

    model_config = ConfigDict(extra="forbid")

    env: Literal["prod", "vps"] = "prod"
    enabled: bool = True
    app_key: str | None = None  # 비밀값: env 오버라이드 전용
    app_secret: str | None = None  # 비밀값: env 오버라이드 전용
    # our_ticker → KIS 6자리 코드. KR 은 대개 항등(005930→005930). 가격과 동일 정책 —
    # 맵에 없는 종목(US 등)은 이 소스가 건너뛴다(KIS 는 국내 전용).
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


class EtfSource(BaseModel):
    """ETF 구성종목(holdings) 소스 — FMP ETF holdings 스냅샷 수집 (US, S005 선행).

    api_key 는 커밋되는 파일이 아니라 환경변수로 주입한다:
        DATA_PIPELINE_ETF__SOURCE__API_KEY=...
    base_url 은 FMP `/stable/etf/holdings` 엔드포인트. 날짜창 없이 심볼(ETF)당 1콜로
    현재 구성종목 전량을 받는다(가격·재무처럼 배열 응답, 뉴스처럼 페이지네이션 없음).
    """

    model_config = ConfigDict(extra="forbid")

    base_url: NonBlankStr
    enabled: bool = True
    api_key: str | None = None  # 비밀값: env 오버라이드 전용
    # our_etf_id → FMP ETF 심볼. 종목(symbol_map)과 별개 맵이다 — 수집 대상이 종목
    # 유니버스가 아니라 ETF 목록이라, 이 맵의 키가 곧 수집 유니버스다(targets 와 무관).
    # 매핑 없는 ETF 는 수집하지 않는다(생략 = 제외). KR ETF 는 FMP 커버리지 밖이라
    # 여기 두지 않는다(후속 KIS 등 별도 벤더 — ALPHA-336).
    etf_map: dict[str, NonBlankStr] = Field(default_factory=dict)


class KrxEtfSource(BaseModel):
    """KRX 정보데이터시스템 ETF 구성종목(PDF) 소스 — 로그인 게이트 뒤 MDCSTAT05001 (KR, ALPHA-336).

    US(EtfSource=FMP holdings)와 달리 (1) 인증이 KRX 계정 로그인(mbr_id/pw, JSESSIONID
    세션) (2) KR 시장 전용이라 market 은 항상 KR (3) etf_map 이 our_etf_id → ISIN(표준코드,
    예 KR7069500007)이다. 비밀값(mbr_id/pw)은 커밋되는 파일이 아니라 환경변수로 주입한다:
        DATA_PIPELINE_KRX_ETF__SOURCE__MBR_ID=...
        DATA_PIPELINE_KRX_ETF__SOURCE__PW=...
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    mbr_id: str | None = None  # 비밀값: env 오버라이드 전용
    pw: str | None = None  # 비밀값: env 오버라이드 전용
    # our_etf_id → KRX ISIN(표준코드). US(FMP 심볼)와 달리 KRX 는 ISIN 으로 질의한다. 이 맵의
    # 키가 곧 수집 유니버스다(targets 무관) — 매핑 없는 ETF 는 수집하지 않는다(생략 = 제외).
    etf_map: dict[str, NonBlankStr] = Field(default_factory=dict)


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


class DartDisclosureSource(BaseModel):
    """OpenDART 국내 공시(disclosure filing) 소스 (disclosures raw).

    재무제표(DartFinancialSource, fnlttSinglAcnt)와 **다른 API**다 — 이 소스는 공시목록
    (list.json)과 공시서류 원본(document.xml)을 다룬다. api_key 는 커밋되는 파일이 아니라
    환경변수로 주입한다:
        DATA_PIPELINE_DART_DISCLOSURE__SOURCE__API_KEY=...

    ⚠️ 재무 소스와 달리 **corp_code 매핑을 쓰지 않는다** — 날짜창의 시장 전체 목록을 받아
    stock_code 로 거른다(`sources/dart_disclosure.py`). corpCode.xml 은 enrich-corp-code
    스텝만 쓴다.
    """

    model_config = ConfigDict(extra="forbid")

    base_url: NonBlankStr = "https://opendart.fss.or.kr/api"
    enabled: bool = True
    api_key: str | None = None  # 비밀값: env 오버라이드 전용
    # our_ticker → KRX 6자리 종목코드. 매핑 없는 심볼은 이 소스가 건너뛴다(재무와 동일 정책).
    symbol_map: dict[str, NonBlankStr] = Field(default_factory=dict)
    # 수집 대상 공시 유형 — report_nm 부분일치(strip 후)로 거른다. 공시목록은 전 유형을
    # 주므로 이 필터로 좁힌다(예: "단일판매ㆍ공급계약체결" 은 "공급계약" 으로 매칭). 실측상
    # 가운뎃점(ㆍ)·꼬리 공백·[기재정정] 접두가 있어 부분일치가 정정본까지 안전히 잡는다.
    report_name_filters: list[NonBlankStr] = Field(
        default_factory=lambda: ["공급계약", "사업보고서"]
    )
    page_count: int = Field(default=100, ge=1, le=100)  # list.json 페이지당 건수(최대 100)
    # 창 전체 페이지 상한 — **폭주 가드**지 절단 정책이 아니다(순회 종료는 total_page 가 정한다).
    # 종목별 질의를 걷어내면서 축이 corp 당에서 창 전체로 바뀌었다: 시장 전체 공시는 하루
    # 700~1,070건(실측)이라 기본 증분 창(어제~오늘)만도 ~18 페이지다. 옛 상한 10 을 그대로 두면
    # **평소 런이 매번 절단**된다. 백필 창(수십 일)도 이 안에 들어오게 넉넉히 잡는다.
    max_pages: int = Field(default=500, ge=1, le=5000)


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


class YahooPriceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: YahooPriceSourceConfig


class KisNavConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: KisNavSource


class KisInvestorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: KisInvestorSource


class FinancialConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: FinancialSource


class EtfConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: EtfSource


class KrxEtfConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: KrxEtfSource


class DartFinancialConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: DartFinancialSource


class DartDisclosureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: DartDisclosureSource


class MinuteRelayConfig(BaseModel):
    """1분 Outbox Relay 설정 — `relay` 스텝만 쓴다(ALPHA-670).

    큐 URL 은 환경(dev·staging)마다 다르므로 동봉 sources.toml 이 아니라 env 로 온다 —
    **JSON 한 변수**다(JSON 전체를 홑따옴표로 감싼다 — 안 감싸면 셸이 안쪽 따옴표를
    먹어 로더가 파싱에 실패한다):
    `DATA_PIPELINE_MINUTE_RELAY__QUEUE_URLS='{"<destination>":"<url>",…}'`.
    nested 형태(`…__QUEUE_URLS__<destination>`)는 쓰지 않는다 — destination 이름에
    하이픈이 있어 셸이 변수 할당으로 파싱하지 못한다(실증: `command not found`).
    destination 은 outbox 행이
    들고 있는 값이고(큐 4종: price-analysis-realtime·news-extraction-realtime·
    news-extraction-backfill + 트리거 설명 price-explanation-realtime — ALPHA-709),
    매핑에 없는 destination 의 event 는 Relay 가 DEAD 로 격리한다 — 프로세스를
    죽이면 멀쩡한 다른 큐까지 멈추기 때문이다.
    """

    model_config = ConfigDict(extra="forbid")

    queue_urls: dict[NonBlankStr, NonBlankStr]
    batch_limit: int = Field(default=10, ge=1, le=10)  # SQS SendMessageBatch 상한
    # 상한을 둔다 — 큰 값(10^15)은 pydantic 을 통과한 뒤 timedelta 범위를 넘겨 claim
    # 전에 매번 crash 하고, 설정이 그대로면 재기동해도 같은 자리에서 죽는다.
    # 기본 150초 = batch_limit(10) × SQS 호출 예산(15초). 발행이 lease 보다 오래 걸리면
    # 경쟁 Relay 가 같은 행을 탈취한다(minute/relay.py __post_init__ 이 조합을 검증한다).
    lease_seconds: int = Field(default=150, ge=15, le=3600)
    retry_base_seconds: int = Field(default=2, ge=1, le=3600)
    retry_max_seconds: int = Field(default=300, ge=1, le=86_400)
    # tick 사이 대기(초). ECS 상주 서비스라 짧게 돈다 — 발행 지연 목표는 수초(v0.7 11.1).
    # 상한을 둔다: `1e309` 는 inf 로 파싱돼 gt=0 을 통과하고 time.sleep(inf) 가
    # OverflowError 를 내며, 설정이 그대로면 재기동해도 같은 자리에서 죽는다.
    tick_seconds: float = Field(default=1.0, gt=0, le=60)


class MinuteConsumerConfig(BaseModel):
    """1분 Consumer 운영 설정 — 현재는 `dlq-reconcile` 만 쓴다(ALPHA-672).

    큐 URL 은 환경마다 다르므로 env 로 온다 — `MINUTE_RELAY__QUEUE_URLS` 와 같은 이유로
    **JSON 한 변수**다(destination 이름에 하이픈이 있어 nested 형태를 셸이 못 파싱한다).
    JSON 전체를 홑따옴표로 감싸야 셸이 안쪽 따옴표를 먹지 않는다. 큐 어휘 3종을
    **전부** 채워야 한다(하나라도 빠지면 그 레인은 아무도 대사하지 않으므로 기동 거부):
    `DATA_PIPELINE_MINUTE_CONSUMER__DLQ_URLS='{"price-analysis-realtime":"<url>",
    "news-extraction-realtime":"<url>","news-extraction-backfill":"<url>"}'`.

    ⚠️ 여기 **원 큐 URL 을 넣으면 안 된다** — reconciler 가 정상 배달을 전부 "DLQ 도착"
    으로 읽어 살아 있는 job 을 DEAD 로 만든다. 그래서 `dlq-reconcile` 은 relay 큐 매핑을
    함께 요구하고 겹치면 기동을 거부한다(minute/consumer.py).
    """

    model_config = ConfigDict(extra="forbid")

    # 빈 매핑은 거부한다 — 검증을 통과한 뒤 reconciler 가 큐를 하나도 안 보고 성공으로
    # 끝나, 실제 DLQ 의 non-terminal job 이 남아도 운영 게이트가 초록이 된다(Rule 12).
    dlq_urls: dict[NonBlankStr, NonBlankStr] = Field(min_length=1)
    batch_size: int = Field(default=10, ge=1, le=10)  # SQS ReceiveMessage 상한
    wait_seconds: int = Field(default=20, ge=0, le=20)  # long polling 상한
    # 대사 중 그 메시지를 다른 실행이 다시 집지 않을 만큼만. 지우지 않으므로 이 시간이
    # 지나면 다시 보인다(판정은 멱등이라 무해하다).
    visibility_seconds: int = Field(default=60, ge=1, le=43_200)


class MinutePriceWorkerConfig(BaseModel):
    """1분 Price Worker 상주 설정 — `price-worker` 스텝만 쓴다(ALPHA-706).

    토스 자격증명은 커밋되는 파일이 아니라 환경변수로 주입한다:
        DATA_PIPELINE_MINUTE_PRICE_WORKER__CLIENT_ID=...
        DATA_PIPELINE_MINUTE_PRICE_WORKER__CLIENT_SECRET=...
    universe 파일 경로는 설정이 아니라 CLI 인자(`--universe`)다 — planner
    (`plan-minute-session`)와 같은 파일을 받아야 원장 universe 와 일치한다.
    """

    model_config = ConfigDict(extra="forbid")

    client_id: str | None = None  # 비밀값: env 오버라이드 전용
    client_secret: str | None = None  # 비밀값: env 오버라이드 전용
    source: NonBlankStr = "toss"
    # price job identity 축 — 판정 규칙(축·임계)이 바뀌면 이 값을 올려 새 job 이 생기게
    # 한다. 기본값을 두지 않는다: 배포마다 조용히 같은 값이면 규칙 변경이 identity 에
    # 안 드러난다.
    trigger_schema_version: NonBlankStr
    destination: NonBlankStr = "price-analysis-realtime"
    # 한 콜로 몇 분까지 거슬러 받을지(TossPriceCollector.lookback, count 상한 200)
    lookback: int = Field(default=1, ge=1, le=200)
    # window claim lease — 토스 tick 실측 상한(363종 ÷ 초당 5회 ≈ 73초+) **위**여야 한다.
    # 짧으면 자기 claim 이 in-flight 중 만료돼 recovery lane 이 같은 window 를 재청구하고
    # 원래 attempt 의 commit 이 통째로 거부된다(ALPHA-706 — 하한 90 이 그 가드다).
    lease_seconds: int = Field(default=300, ge=90, le=3600)
    session_lease_seconds: int = Field(default=300, ge=60, le=3600)
    heartbeat_every_seconds: int = Field(default=60, ge=5, le=300)
    # 하한 1 — DRAINING 수렴은 recovery lane 만 연다(만료 고아 CLAIMED 회수). 0 이면
    # 실패 잔존 CLAIMED 를 아무도 회수하지 못해 ack_drain 이 영구 거부되고 세션이
    # DRAINING 에 고착된다(worker.tick 의 drain 분기).
    recovery_budget_per_tick: int = Field(default=2, ge=1, le=50)
    # IDLE/DRAINING 일 때 tick 사이 대기(초). window 는 60초마다 생기므로 짧은 폴링이면
    # 충분하다. 상한을 둔다 — inf 는 time.sleep 에서 OverflowError(MinuteRelayConfig 동형).
    tick_seconds: float = Field(default=5.0, gt=0, le=60)

    @model_validator(mode="after")
    def _leases_cover_worst_tick(self) -> MinutePriceWorkerConfig:
        """lease 는 **한 tick 의 최악 소요** 위여야 한다.

        한 tick 은 최대 (realtime 1 + recovery budget) 개 window 를 순차 처리하고,
        claim 의 lease_expires_at 은 전부 **tick 시작 시각** 기준이다(가상 시계 계약 —
        tick 안에서 시계를 다시 읽지 않는다). 토스 window 하나가 실측 73초+ 라, 이
        여유(75초/window)를 넘게 잡으면 뒤쪽 claim 이 처리 중 만료돼 다른 attempt 가
        탈취하고 원래 commit 이 거부된다. session fence 도 같은 축이다 — heartbeat 은
        tick 경계에서만 돌므로 fence lease 가 tick 최악보다 짧으면 처리 중 만료된다.

        ⚠️ 75초/window 는 **하한 가드지 상한 증명이 아니다** — 재시도(콜당 최대 3회)·
        timeout(10초) 폭주가 겹치면 한 window 가 이를 넘을 수 있다. 그 경우에도
        정확성은 claim/fence CAS 가 지킨다(탈취된 attempt 의 commit 이 거부될 뿐,
        이중 커밋은 없다) — 이 검증은 명백히 틀린 설정을 배포 전에 거르는 장치다.
        """
        worst_tick = (1 + self.recovery_budget_per_tick) * 75
        if self.lease_seconds < worst_tick:
            raise ValueError(
                f"lease_seconds({self.lease_seconds}) < tick 최악 소요({worst_tick}초 = "
                f"(1+recovery_budget {self.recovery_budget_per_tick}) × 75) — "
                "in-flight claim 이 만료된다. budget 을 줄이거나 lease 를 늘려라"
            )
        # fence 갱신은 tick 경계에서만 된다 — 최악은 "직전 갱신 후 heartbeat 주기
        # 직전(주기−ε)에 시작한 tick 이 최악 소요만큼 도는" 경우라, lease 는 두 구간의
        # 합을 덮어야 한다. 절반 규칙(×2)은 tick 소요를 무시해 이 조합을 통과시킨다.
        if self.heartbeat_every_seconds + worst_tick > self.session_lease_seconds:
            raise ValueError(
                f"session_lease_seconds({self.session_lease_seconds}) < heartbeat 주기"
                f"({self.heartbeat_every_seconds}) + tick 최악 소요({worst_tick}초) — "
                "fence 가 처리 중 만료돼 정상 수집이 거부된다"
            )
        return self


class MinutePriceConsumerConfig(BaseModel):
    """1분 가격 판정 Consumer 상주 설정 — `price-consumer` 스텝만 쓴다(ALPHA-711).

    kernel 수치(batch·visibility·heartbeat·lease·재시도)는 여기서 **검증하지 않는다**
    — `minute.consumer.ConsumerConfig.__post_init__` 가 조합 검증의 정본이다(두 벌이면
    한쪽만 고쳐진다). 임계(abs_threshold)는 `price_triggers` 섹션을 재사용한다.
    """

    model_config = ConfigDict(extra="forbid")

    queue_url: NonBlankStr
    # 판정 규칙의 identity 축 — 일 단위(prev_close 대비)와 축이 달라 기본값을 두지
    # 않는다(배포마다 조용히 같은 값이면 규칙 변경이 identity 에 안 드러난다)
    detection_policy_version: NonBlankStr
    destination: NonBlankStr = "price-explanation-realtime"
    batch_size: int = 10
    wait_seconds: int = 20
    visibility_seconds: int = 300
    heartbeat_seconds: int = 60
    max_concurrency: int = 1
    lease_seconds: int = 600
    retry_base_seconds: int = 5
    retry_max_seconds: int = 900
    max_attempts: int = 5


class PriceTriggersConfig(BaseModel):
    """ETF 가격변동 트리거 산출 설정 — load-price-triggers 만 쓴다(ALPHA-406).

    absolute gate 하나만 잠정 구현한다 — 임계값·relative gate 규칙은 로직 소유자 합의
    대상이라 코드 상수가 아닌 설정으로 두고, 적재 행의 detection_policy_version 에
    잠정 정책 이름을 박아 나중에 어느 정책으로 만든 행인지 식별하게 한다.
    """

    model_config = ConfigDict(extra="forbid")

    market: NonBlankStr = "KR"
    # 트리거 유니버스는 holdings∩마스터에서 파생한다 — 설정에 티커를 두지 않는다(정본은
    # [krx_etf] 한 곳). etf_ticker 는 **옵션 단일 실행 필터**: 지정하면 그 한 종만 돈다
    # (dev 검증·백필용). 미지정(기본 None)이면 유니버스 전체(ALPHA-465).
    etf_ticker: NonBlankStr | None = None
    abs_threshold: float = Field(gt=0, lt=1)  # 일수익률 절대값 게이트(예: 0.005 = 0.5%)
    policy_version: NonBlankStr


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


class DbConfig(BaseModel):
    """Cloud Event Store(Postgres) 접속. 적재 스텝(load-*)만 쓴다.

    스토리지(StorageConfig)와 같은 결의 **인프라 설정**이라 같은 네임스페이스에 둔다:
        DATA_PIPELINE_DB__HOST=... DATA_PIPELINE_DB__PASSWORD=...

    비밀번호는 **파일·설정에 두지 않는다** — env 주입만 허용한다(배포는 RDS 관리형 시크릿을
    task 로 주입, 로컬은 SSM 포트포워딩 + 시크릿에서 꺼내 넣는다).

    `sslmode` 기본 require — dev RDS 는 private 서브넷이지만 평문으로 흘릴 이유가 없다
    (analysis-engine upload_ff5_rds.py 도 같은 전제).
    """

    model_config = ConfigDict(extra="forbid")

    host: NonBlankStr = "127.0.0.1"
    port: int = 5432
    name: NonBlankStr = "edge"
    user: NonBlankStr = "edge"
    password: str | None = None
    sslmode: NonBlankStr = "require"

    @model_validator(mode="after")
    def _require_password(self) -> DbConfig:
        # 비밀번호 없이 부팅하면 첫 커넥션에서야 죽는다 — 로드 시점에 fail loud(StorageConfig 동형).
        if not (self.password or "").strip():
            raise ValueError("db.password 가 없다 — DATA_PIPELINE_DB__PASSWORD 로 주입한다")
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
