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
    # 기본 160·상한 200 — 스케줄 런의 창이 `[어제, 오늘]` 2일이라 실측 요구가 108~126 page 다
    # (근거 SSOT 는 sources.toml 의 max_pages 주석). 종전 상한 100 은 하루 창을 전제한 값이라
    # 올바른 값을 **설정조차 할 수 없었고**, 종전 기본 40 은 하루치의 2/3라 설정에서 이 키가
    # 빠지는 순간 매 런이 절단된다 — 기본값도 창에 맞춘다. 200 은 그 위의 폭주 방지선이다.
    max_pages: int = Field(default=160, ge=1, le=200)
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
    # 참조 계열 ETF 의 명부 — **holdings 만 받고 유니버스 파생에는 안 들어간다**(ALPHA-855).
    #
    # 왜 `etf_map` 이 아닌 별도 맵인가: `etf_map` 의 키는 KRX 수집 대상이자 **유니버스 파생의
    # 뿌리**다(`ingest_price_raw._krx_expected_etfs`). 넣으면 일봉 가격·투자자 수급·공시 제외
    # 집합·1분 `constituent_ids` 넷이 같이 는다 — 48종의 구성종목 합집합이 1,000종 규모라
    # 1분 수집이 410 → 1,400 unit 로 뛰고(226490 KODEX 코스피 혼자 725종) KIS 실측 상한
    # (60초 창 약 890 unit)을 넘긴다. 이 맵은 어느 파생에도 안 들어간다: `plan()` 만 두 맵의
    # 합집합을 보고, `_krx_expected_etfs` 는 `etf_map` 만 본다.
    #
    # 받는 이유는 층 분해의 겹침 게이트(`layers.overlap`)다 — 후보 ETF 의 명부·비중이 있어야
    # 동어반복(같은 포트폴리오로 자신을 설명)을 걸러낸다. 없으면 FMP 폴백 하나뿐인데 그
    # 스냅샷이 2026-01-28 단건이다.
    reference_etf_map: dict[str, NonBlankStr] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_maps_are_disjoint(self) -> KrxEtfSource:
        # 같은 ETF 가 양쪽에 있으면 `plan()` 이 그 ETF 를 두 번 계획해 KRX 를 두 번 부른다.
        # 그보다 큰 문제는 **두 선언이 서로 다른 말을 한다**는 것이다 — `etf_map` 은 "유니버스
        # 뿌리로 삼아라", 이쪽은 "명부만 받아라". 조용히 한쪽으로 넘기면 나머지 한쪽이 거짓이
        # 되므로 평균내지 않고 거부한다(Rule 7). 어느 쪽을 지울지는 사람이 정할 일이다.
        # `build_minute_universe` 가 etf_map ↔ sector_etf_ids 에 건 것과 같은 규율이다.
        if both := sorted(set(self.etf_map) & set(self.reference_etf_map)):
            raise ValueError(
                f"같은 ETF 가 etf_map 과 reference_etf_map 양쪽에 있다: {both} "
                f"— 유니버스 뿌리면 etf_map 에만, 참조 계열이면 reference_etf_map 에만 둬라"
            )
        return self


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


class KrxInstrumentSource(BaseModel):
    """KRX OpenAPI 종목기본정보 소스 — 상장 전종목 단축코드·한글명 (ALPHA-829).

    ⚠️ **`krx_etf` 와 다른 서비스다.** 저쪽은 `data.krx.co.kr` 비공식 경로 + 계정 로그인
    (mbr_id/pw → JSESSIONID)이고, 여기는 `data-dbg.krx.co.kr` 공식 OpenAPI + 무상태
    `AUTH_KEY` 헤더다. 자격증명이 서로 대체되지 않으니 설정도 섞지 않는다.

    auth_key 는 커밋되는 파일이 아니라 환경변수로 주입한다:
        DATA_PIPELINE_KRX_INSTRUMENT__SOURCE__AUTH_KEY=...
    (운영은 Secrets Manager `edge-dev-data-pipeline/krx/api-key` 의 `authkey` 를 넣는다.)
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    auth_key: str | None = None  # 비밀값: env 오버라이드 전용


class KrxInstrumentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: KrxInstrumentSource


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

    자격증명은 커밋되는 파일이 아니라 환경변수로 주입한다. **소스마다 쌍이 다르다** —
    한 쌍으로 겸용하면 어느 벤더의 키가 들었는지 이름이 말해주지 않는다:
        source=kis  → DATA_PIPELINE_MINUTE_PRICE_WORKER__APP_KEY / __APP_SECRET
        source=toss → DATA_PIPELINE_MINUTE_PRICE_WORKER__CLIENT_ID / __CLIENT_SECRET
    universe 파일 경로는 설정이 아니라 CLI 인자(`--universe`)다 — planner
    (`plan-minute-session`)와 같은 파일을 받아야 원장 universe 와 일치한다.
    """

    model_config = ConfigDict(extra="forbid")

    client_id: str | None = None  # 비밀값(토스): env 오버라이드 전용
    client_secret: str | None = None  # 비밀값(토스): env 오버라이드 전용
    app_key: str | None = None  # 비밀값(KIS): env 오버라이드 전용
    app_secret: str | None = None  # 비밀값(KIS): env 오버라이드 전용
    # 기본은 kis 다(ALPHA-735) — 토스는 초당 5회라 종목당 1콜 × 400종이 60초 창을 넘는다.
    # ⚠️ terraform `minute_session_source_group` 과 **같은 값**이어야 한다(session_id 유도 축).
    source: NonBlankStr = "kis"
    # KIS 호출 간격(초). 기본 0.08 → 12.5 req/s. 그 유량은 **앱키 단위 전역**이라 다른 kis
    # 스텝과 나눠 쓴다 — 경합이 보이면 재배포 없이 env 로 올린다. toss 소스에는 쓰이지
    # 않는다(어댑터가 자기 상수).
    #
    # ⚠️ **마진 근거가 ALPHA-769 로 바뀌었다.** 종전엔 경합 상대가 15:40 배치뿐이라 시간대가
    # 안 겹치는 것이 마진의 근거였다(1분 세션은 15:30 종료). 이제 장중 수급 레인이 세션
    # **안**에서 돈다 — 슬롯 5개(09:35·10:05·11:25·13:25·14:35)가 전부 07:45~15:30 구간이고,
    # 같은 앱키다(tasks.tf 배치 kis ↔ minute_services.tf 워커가 같은 시크릿).
    # 그 수집기는 `KIS_MIN_INTERVAL_SEC=0.5`(run.py) → 2 req/s 이고 슬롯당 ~210초 동안 돈다.
    # 즉 하루 5회, 각 ~3.5분씩 제시 유량이 12.5+2 = **14.5 req/s** 다.
    # ponytail: 헤드룸 0.5 req/s. 여기서 재는 축은 **레포 예산선 15/s** 이지 벤더 한도가
    # 아니다 — EGW00201 이 세는 한도는 앱키당 **20/s** 이고, 레포가 그 아래 15/s 를 예산으로
    # 잡는다(`sources/http.py _respect_interval` — pace 가 발신 시각을 보장하지 못하는 대가를
    # 마진으로 치른다). 14.8 req/s 는 1분 수집기의 **달성 실측치**(kis_minute)지 거절 임계가
    # 아니다. 1분 레인에 EGW00201 이 보이기 시작하면 여기(0.08)를 먼저 늘려라 — 장중 수급은
    # 하루 5회뿐이라 그쪽 간격을 늘리는 것보다 값이 크다.
    min_interval_sec: float = Field(default=0.08, gt=0, le=5)
    # price job identity 축 — 판정 규칙(축·임계)이 바뀌면 이 값을 올려 새 job 이 생기게
    # 한다. 기본값을 두지 않는다: 배포마다 조용히 같은 값이면 규칙 변경이 identity 에
    # 안 드러난다.
    trigger_schema_version: NonBlankStr
    destination: NonBlankStr = "price-analysis-realtime"
    # 한 콜로 몇 분까지 거슬러 받을지(TossPriceCollector.lookback, count 상한 200)
    lookback: int = Field(default=1, ge=1, le=200)
    # window claim lease — tick 최악 소요 **위**여야 한다(아래 검증의 75초/window 축).
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
        tick 안에서 시계를 다시 읽지 않는다). window 하나의 여유를 75초로 잡는다(토스
        실측 73초+ 가 근거였고, KIS 는 410 unit ÷ 12 req/s ≈ 34초라 같은 상수 아래로
        들어온다 — ALPHA-735·842. unit 은 판정 ETF + 구성종목 + 참조 계열의 합이다). 이를 넘게 잡으면 뒤쪽 claim 이 처리 중 만료돼 다른
        attempt 가 탈취하고 원래 commit 이 거부된다. session fence 도 같은 축이다 — heartbeat 은
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


class MinuteNewsWorkerConfig(BaseModel):
    """1분 News Worker 상주 설정 — `news-worker` 스텝만 쓴다(ALPHA-707).

    엔드포인트·카테고리는 여기 없다 — `[bigkinds_news]`(base_url·category_codes)가
    정본이고 배치 수집과 공유한다(두 벌이면 카테고리가 한쪽만 바뀌어 배치와 1분 레인이
    다른 뉴스를 걷는다). 여기는 **1분 루프의 수치**만 둔다.

    pacing(min_interval_sec)·페이지 예산은 ALPHA-645 실측(2026-08-03)이 기본값의 근거다:
    1분 주기 page1 폴링은 200 연속·RTT p50≈1초대였고, 저녁 신규 유입 0~8건/분이라
    평시 1page(100건)로 충분하다. 차단 시그니처가 보이면 **이 값을 올리는 게**(간격을
    벌리는 게) 처방이다 — 재배포 없이 env 로 바꾼다.
    """

    model_config = ConfigDict(extra="forbid")

    source: NonBlankStr = "bigkinds"
    destination: NonBlankStr = "news-extraction-realtime"
    # 페이지 예산 — NewsWorkerConfig(__post_init__)가 조합 검증의 정본이라 여기선
    # 범위만 건다(recovery ≥ max 는 거기서 걸린다).
    max_pages: int = Field(default=4, ge=1, le=40)
    recovery_max_pages: int = Field(default=8, ge=1, le=40)
    page_size: int = Field(default=100, ge=1, le=100)  # 100 = API 상한(배치와 동일)
    anchor_size: int = Field(default=10, ge=1, le=100)
    # 벤더 요청 간격(초) — PoliteClient.min_interval 로 주입되는 pacing 의 정본.
    min_interval_sec: float = Field(default=1.0, ge=0.2, le=30)
    # 스파이크에서 단발 read 20초 초과를 실측했다(대부분 1~2.5초) — 기본 10초는 느린
    # 꼬리를 실패로 접는다. 벤더가 원래 느린 건 KRX(45초)와 같은 성질이다.
    timeout_sec: float = Field(default=45.0, gt=0, le=120)
    lease_seconds: int = Field(default=240, ge=30, le=3600)
    session_lease_seconds: int = Field(default=300, ge=60, le=3600)
    heartbeat_every_seconds: int = Field(default=60, ge=5, le=300)
    # 가격보다 낮은 기본(1) — backlog window 하나마다 벤더 poll 이 한 번 더 나간다
    # (차단 위험 축). 밀린 분들의 기사는 anchor 목표 poll 이 어차피 함께 걷는다.
    recovery_budget_per_tick: int = Field(default=1, ge=1, le=10)
    tick_seconds: float = Field(default=5.0, gt=0, le=60)
    # 차단 쿨다운(초) — BlockedFeedError 후 poll 억제 시간(NewsWorkerConfig 로 전달).
    block_cooldown_seconds: int = Field(default=300, ge=60, le=3600)

    @model_validator(mode="after")
    def _leases_cover_worst_poll(self) -> MinuteNewsWorkerConfig:
        """lease 는 한 tick 의 최악 소요 위여야 한다(price 워커의 75초/window 와 같은 축).

        한 tick 은 최대 (1 + recovery_budget) 개 window 를 순차 poll 하고, lagging 이면
        poll 하나가 recovery_max_pages 페이지를 읽는다. 페이지당 예산은 간격 + RTT 여유
        5초(스파이크 p50 1.4s·간헐 꼬리는 timeout 이 자른다)로 잡는다 — 정확성은 claim/
        fence CAS 가 지키므로 이 검증은 명백히 틀린 설정을 배포 전에 거르는 조잡한
        게이트다(price _leases_cover_worst_tick 과 같은 성질).
        """
        page_budget = self.min_interval_sec + 5.0
        # poll 당 timeout 1회 정체를 허용치에 넣는다(스파이크에서 단발 read 초과 실측).
        # 페이지 전부가 timeout×재시도로 정체하는 폭주까지는 안 덮는다 — 그 경우의
        # 정확성은 claim/fence CAS 가 지킨다(price 검증자의 "하한 가드" 단서와 동일).
        worst_poll = self.recovery_max_pages * page_budget + self.timeout_sec
        worst_tick = (1 + self.recovery_budget_per_tick) * worst_poll
        if self.lease_seconds < worst_tick:
            raise ValueError(
                f"lease_seconds({self.lease_seconds}) < tick 최악 소요({worst_tick:.0f}초 = "
                f"(1+budget {self.recovery_budget_per_tick}) × recovery_max_pages "
                f"{self.recovery_max_pages} × 페이지 예산 {page_budget:.1f}s) — "
                "in-flight claim 이 만료된다. 페이지 예산을 줄이거나 lease 를 늘려라"
            )
        if self.heartbeat_every_seconds + worst_tick > self.session_lease_seconds:
            raise ValueError(
                f"session_lease_seconds({self.session_lease_seconds}) < heartbeat 주기"
                f"({self.heartbeat_every_seconds}) + tick 최악 소요({worst_tick:.0f}초) — "
                "fence 가 처리 중 만료돼 정상 수집이 거부된다"
            )
        return self


class MinuteDisclosureWorkerConfig(BaseModel):
    """1분 Disclosure Worker 상주 설정 — `disclosure-worker` 스텝만 쓴다(ALPHA-875).

    `[minute_news_worker]` 와 같은 분업이다: 엔드포인트·유형 필터·`page_count`·`max_pages` 의
    정본은 `[dart_disclosure.source]`(배치 수집과 공유)이고, 여기는 **1분 루프의 수치**만 둔다.
    두 벌이면 유형 필터가 한쪽만 바뀌어 배치와 1분 레인이 다른 공시를 걷는다.

    ⚠️ **pacing 이 여기 처음 생긴다.** 종전엔 `run.py` 가 `PoliteClient()` 를 인자 없이 만들어
    (기본 min_interval=1.0) 재배포 없이는 조일 수 없었다 — 공시는 DART 앱키를 세 스텝
    (`ingest-raw-disclosure`·`ingest-raw-financial`·`enrich-corp-code`)과 나눠 쓰고
    `"020" 일 사용한도 초과`가 `STOP_STATUS_CODES` 라, 닿으면 레인이 선다. env 로 조인다:
        DATA_PIPELINE_MINUTE_DISCLOSURE_WORKER__MIN_INTERVAL_SEC=1.5
    """

    model_config = ConfigDict(extra="forbid")

    source: NonBlankStr = "dart"
    # 벤더 요청 간격(초) — PoliteClient.min_interval 로 주입되는 pacing 의 정본.
    # 기본 1.0 은 종전 `PoliteClient()` 무인자 기본과 **같은 값**이다(이 PR 이 유량을 바꾸지
    # 않는다 — 손잡이만 만든다). 올리면 window 하나가 길어지므로 lease 검증이 같이 움직인다.
    min_interval_sec: float = Field(default=1.0, ge=0.2, le=30)
    # 종전 `PoliteClient()` 기본과 같은 10초. list.json 은 실측 ~0.9초/페이지(ALPHA-714 의
    # 슬롯당 6.3초 ÷ 7페이지)라 여유가 크다 — 느린 꼬리가 보이면 여기를 올린다.
    timeout_sec: float = Field(default=10.0, gt=0, le=120)
    # window 하나(=날짜창 전체 재독)가 읽을 list.json 페이지 예산. **벤더의 `page_count` 가
    # 아니다** — 그건 페이지당 건수(100)이고 이건 "한 window 가 몇 페이지를 넘기는가"다.
    #
    # ⭐ 이 값은 **실제 순회 상한으로 주입된다**(`disclosure_worker_cli` 가 이 워커의 소스
    # 설정에 `max_pages` 로 덮어쓴다). 안 그러면 실제 상한은 벤더 섹션의 500(백필용)이고,
    # 아래 검증자는 이 값으로 계산하니 **검증이 초록인데 실제 tick 이 lease 를 넘는다.**
    #
    # 기본 60 의 유도: 평상시 필요분은 하루 700~1,070건(실측) × 2일 ÷ page_count 100 =
    # **14~22 페이지**다. 60 은 그 위로 헤드룸을 둔 값으로 하루 3,000건(실측 피크의 ~3배,
    # 3월 사업보고서 접수 급증 대비)까지 절단 없이 덮는다. 아래 검증자가 이 값으로 lease 를
    # 재므로 올릴 때는 lease·session_lease 가 따라 올라간다 — 그게 이 노브의 대가다.
    # ⚠️ **창 폭·일 건수 파생값**이다. 창을 당일로만 좁히면 필요분이 절반이 되고, 유형 필터를
    # 넓혀도 목록 질의는 전 유형을 훑으니 **안 변한다**(감쇠는 저장 단계에서 일어난다).
    # 상수로 박힌 초(예: 22초)를 쓰지 않는 이유가 이것이다 — 창을 바꾼 순간 낡는다.
    max_pages_per_window: int = Field(default=60, ge=1, le=500)
    # 한 window 가 **새로** 받을 본문(document.xml, 행당 1콜) 예산. 정상 tick 은 0~1건이다:
    # 분당 신규 대상 공시가 그 규모고(실측 한 슬롯 `records_saved_target=1`), 이미 받아 둔
    # 본문은 seen-map 이 재다운로드를 막는다(ALPHA-720 — UTC 2일 창).
    # ⚠️ **콜드 스타트는 이 예산을 넘는다** — seen-map 이 빈 첫 컷오버일·장기 중단 복귀에는
    # 그날 대상 전량을 한 window 가 받는다. 거기까지 덮지 않는 건 의도다: 그 경우의 정확성은
    # claim/fence CAS 가 지키고(탈취된 attempt 의 commit 이 거부될 뿐), 이 검증자는 **평상시**
    # 설정 오류를 거르는 게이트다(뉴스·가격 검증자가 폭주를 안 덮는 것과 같은 단서).
    max_documents_per_window: int = Field(default=5, ge=0, le=1000)
    # window claim lease — tick 최악 소요 **위**여야 한다(아래 검증자). 하한 90 은 가격과
    # 같은 축이고, 기본 300 은 ALPHA-706 이 60 을 기각하고 정한 값이다.
    # window claim lease — tick 최악 소요 **위**여야 한다(아래 검증자). 기본 300 은 가격·뉴스
    # 와 같은 값이고 ALPHA-706 이 60 을 기각하고 정한 축이다. 기본 예산(60페이지)의 최악
    # tick 은 280초라 300 안에 들어온다 — 예산을 올리면 여기도 올려야 load 가 통과한다.
    lease_seconds: int = Field(default=300, ge=90, le=3600)
    # ⚠️ 가격·뉴스의 300 을 **빌려 쓰지 않는다**. fence 는 heartbeat 주기(60) + 최악 tick
    # (280)을 덮어야 하므로 340 이 하한이고, 300 이면 기본 설정이 자기 검증에 걸린다.
    # 공시 window 가 형제들보다 비싼 것(창 전체 재독)이 그대로 이 층에 나타난 값이다.
    session_lease_seconds: int = Field(default=600, ge=60, le=7200)
    heartbeat_every_seconds: int = Field(default=60, ge=5, le=300)
    # 하한 1 — DRAINING 수렴은 recovery lane 만 연다(만료 고아 CLAIMED 회수). 0 이면
    # ack_drain 이 영구 거부돼 세션이 DRAINING 에 고착된다(worker.tick 의 drain 분기).
    # 기본 1 은 뉴스와 같다: backlog window 하나마다 창 전체 재독이 한 번 더 나가므로
    # (벤더 콜이 가장 비싼 축) 가격의 2 를 빌려 쓰지 않는다.
    recovery_budget_per_tick: int = Field(default=1, ge=1, le=10)
    tick_seconds: float = Field(default=5.0, gt=0, le=60)

    @model_validator(mode="after")
    def _leases_cover_worst_tick(self) -> MinuteDisclosureWorkerConfig:
        """lease 는 한 tick 의 최악 소요 위여야 한다(뉴스 `_leases_cover_worst_poll` 동형).

        ⚠️ **한 tick 은 window 하나가 아니다** — 공용 골격이 realtime 1 + `recovery_budget_per_tick`
        을 한 tick 안에서 순차 처리한다(`worker.MinuteWorkerLoop.tick`). window 당 값을 tick
        예산으로 쓰면 여유가 있다는 결론이 나오는데 실제로는 초과다.

        window 하나 = 목록 `max_pages_per_window` 페이지 + 신규 본문 `max_documents_per_window`
        건이고, **둘 다 같은 `PoliteClient` 를 쓴다**(간격이 합쳐 걸린다).

        콜당 예산 = 간격 + RTT 여유 **1초**. 뉴스 검증자의 5초를 빌려 오지 않는다 — 그 상수는
        BigKinds RTT p50 1.4초·스파이크 20초+ 실측에서 나온 값이고, DART list.json 은 실측
        **슬롯당 6.3초 ÷ 7페이지 ≈ 0.9초/페이지**(ALPHA-714)로 min_interval 1.0 에 이미 묶여
        있다(RTT 가 간격 대기 안에 흡수된다). 5초를 쓰면 페이지가 22개라 22배로 부풀어, 정상
        설정이 거부된다 — 형제의 상수를 층 확인 없이 투사하면 나는 오류다.

        정확성은 이 검증이 지키지 않는다 — claim/fence CAS 가 지킨다(탈취된 attempt 의 commit
        이 거부될 뿐 이중 커밋은 없다). 이건 명백히 틀린 설정을 **load 시점**에 거르는 게이트다.
        현 SFN 은 슬롯 간격 3600초가 이 축을 통째로 가리고 있었다.
        """
        call_budget = self.min_interval_sec + 1.0
        worst_window = (
            (self.max_pages_per_window + self.max_documents_per_window) * call_budget
            + self.timeout_sec
        )
        worst_tick = (1 + self.recovery_budget_per_tick) * worst_window
        if self.lease_seconds < worst_tick:
            # 분해식이 인쇄된 숫자를 **재현해야** 한다 — timeout 은 곱셈 안에 있다(window
            # 하나당 1회). 밖에 있는 것처럼 적으면 운영자가 다른 값을 유도한다.
            raise ValueError(
                f"lease_seconds({self.lease_seconds}) < tick 최악 소요({worst_tick:.0f}초 = "
                f"(1+budget {self.recovery_budget_per_tick}) × ((페이지 "
                f"{self.max_pages_per_window} + 본문 {self.max_documents_per_window}) × 콜 "
                f"예산 {call_budget:.1f}s + timeout {self.timeout_sec:.0f}s) = "
                f"{1 + self.recovery_budget_per_tick} × {worst_window:.0f}s) — "
                "in-flight claim 이 만료된다. 창/예산을 줄이거나 lease 를 늘려라"
            )
        if self.heartbeat_every_seconds + worst_tick > self.session_lease_seconds:
            raise ValueError(
                f"session_lease_seconds({self.session_lease_seconds}) < heartbeat 주기"
                f"({self.heartbeat_every_seconds}) + tick 최악 소요({worst_tick:.0f}초) — "
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


class MinuteNewsConsumerConfig(BaseModel):
    """1분 뉴스 추출 Consumer 상주 설정 — `news-consumer` 스텝만 쓴다(ALPHA-713).

    kernel 수치는 여기서 검증하지 않는다 — `minute.consumer.ConsumerConfig.__post_init__`
    가 조합 검증의 정본이다(MinutePriceConsumerConfig 와 같은 단서). LLM 자격증명은
    이 섹션이 아니라 env(`LLM_API_KEY` 등, tag-news 관례)다 — 커밋되는 TOML 금지.

    queue_url 이 **하나**인 이유: realtime·backfill 은 핸들러가 같고 큐만 달라,
    같은 스텝을 큐 URL 만 바꿔 서비스 2개로 띄운다(커널 무수정 — ConsumerConfig 동형).
    """

    model_config = ConfigDict(extra="forbid")

    queue_url: NonBlankStr
    batch_size: int = 10
    wait_seconds: int = 20
    visibility_seconds: int = 300
    heartbeat_seconds: int = 60
    max_concurrency: int = 1
    lease_seconds: int = 600
    retry_base_seconds: int = 5
    retry_max_seconds: int = 900
    max_attempts: int = 5


class MinuteUniverseConfig(BaseModel):
    """1분 레인 universe.json 을 **만들 때만** 쓰는 설정 — `build_minute_universe` 전용.

    ⚠️ 이 섹션은 수집 유니버스의 정본이 아니다. 1분 레인의 정본은 S3 객체
    `config/minute/universe.json` 이고(planner·worker·consumer 가 `--universe` 로 같은
    객체를 본다), 여기 값은 그 객체를 **생성**하는 입력 하나일 뿐이다 — 이 파일만 고치고
    S3 를 안 갈면 아무것도 안 바뀐다.

    `[krx_etf.source.etf_map]` 과 다른 축이다: 저기는 "KRX PDF 로 holdings 를 받을 ETF"
    이고 **그 구성종목이 유니버스로 파생된다**. 여기 ETF 는 분봉을 받고, holdings 도
    `[krx_etf.source.reference_etf_map]` 으로 따로 받는다(ALPHA-855 — 층 분해의 겹침 게이트가
    명부를 쓴다). 받지 않는 것은 **구성종목 수집·NAV·트리거 판정**이다. 그래서 etf_map 에 넣으면 안 되고
    (KRX PDF 수집이 늘고 구성종목이 유니버스로 딸려 들어온다) `Universe.etf_ids` 에도
    넣으면 안 된다(그 축은 `price_consumer` 의 판정 집합이다 — `Universe` 도크스트링).
    빌더가 이 목록을 `Universe.sector_etf_ids` 로 싣는다.
    """

    model_config = ConfigDict(extra="forbid")

    # 층 분해의 **섹터 후보** ETF — 분봉이 있어야 구간(장중) 모드에서 섹터층이 선다.
    # 섹터의 정본은 KRX 업종지수이고 그 1분봉은 ALPHA-887 이 수집하지만(dataset
    # `sector_index_minute`) 소비 배선이 아직 없어(analysis `layers.select_sector` 의
    # 후보는 섹터 ETF 뿐이다) 그 자리를 이 목록이 메운다.
    # ⚠️ "일봉 경로가 업종지수를 주입한다"·`layers._krx_sector_candidate` 는 **둘 다 없다**
    # — #657 이 일 모드를 걷어냈고 그 함수도 함께 사라졌다.
    #
    # 정본은 analysis 쪽 `layers_daily` 의 `kind='sector'` 집합이고, **여기와 자동으로
    # 맞춰지지 않는다**(그 parquet 은 이 레포 밖 산출물이라 로드 시점에 대조할 수 없다).
    # 갈렸을 때의 증상은 조용하다: `layers._series` 가 `layers_daily` 회원만 후보로
    # 삼으므로 여기 없는 후보는 애초에 `xs` 에 안 들어가고, 탈락 사유를 남기는 자리
    # (`twins`·`alien`·`rho_blocked`)는 **들어온 후보만** 기록한다. 사유 없는 부재다.
    sector_etf_ids: tuple[NonBlankStr, ...] = ()

    @model_validator(mode="after")
    def _validate(self) -> MinuteUniverseConfig:
        from ..parse import krx_short_code  # 지연 import — config 는 parse 에 의존하지 않는다

        # 중복을 여기서 막는 이유: **빌더는 집합 연산이라 조용히 삼킨다.** 아래 하류가
        # 거부해 줄 것이라고 기대하면 안 된다 — 중복 한 줄은 대개 "다른 코드를 적으려다
        # 덮어썼다"의 흔적이라, 삼켜지면 의도한 종목 하나가 통째로 사라진다.
        if len(set(self.sector_etf_ids)) != len(self.sector_etf_ids):
            raise ValueError("sector_etf_ids 에 중복 코드가 있다")
        # **형태만 본다.** `krx_short_code` 는 정규식(선두 숫자 + 영숫자 대문자 6자)이라
        # 실재 여부는 모른다 — 자릿수를 바꿔 적은 오타(`091170`→`091710`)는 그대로
        # 통과하고, 그건 첫 런의 window manifest 에 `missing` 으로 드러난다(etf_map 의
        # ⚠ 항목이 "첫 런 로그에 드러난다"고 적은 것과 같은 성질). 여기서 막는 것은
        # 한글·공백·길이 오류처럼 **로드 시점에 확실히 아는 것**뿐이다. 실재 대조를
        # 하려면 종목 마스터가 필요한데 그건 레이크에 있고 config 로드 경로가 아니다.
        if bad := [c for c in self.sector_etf_ids if krx_short_code(c) is None]:
            raise ValueError(f"KRX 단축코드 형태가 아닌 값이 있다: {bad[:5]}")
        return self


class MinuteSectorIndexConfig(BaseModel):
    """KRX 업종지수 45종의 **수집 대상 정본** — 1분 레인 sector_index dataset (ALPHA-887).

    `[minute_universe]` 와 축이 다르다: 저기는 universe.json 을 **만드는 입력**이고 실제
    정본은 S3 객체지만, 여기 값은 그 자체가 정본이다(이 dataset 은 universe 를 쓰지
    않는다 — `UNIVERSE_DATASETS` 밖이라 planner 가 `--universe` 를 거부한다). 기대 집합이
    config 이므로 이미지 배포가 곧 반영이고, S3 를 갈 일이 없다.

    🔴 **KIS 지수코드는 KRX 업종코드가 아니다.** 그래서 목록이 아니라 **맵**이다. KIS 의
    `U` 네임스페이스는 자체 조밀 번호라(`0xxx`=KOSPI 업종 · `1xxx`=KOSDAQ 업종 ·
    `2xxx`=KOSPI200 계열) KRX 코드와 산술 관계가 없다 — KOSPI 1005~1027 만 우연히
    −1000 이고 1045~1047 에서 꺾인다(0028~0030).

    ⚠️ **틀린 값은 조용히 틀린다.** KRX 코드를 그대로 넣어도 KIS 는 `rt_cd=0` 에 그럴듯한
    한글 업종명이 담긴 **남의 지수**를 준다(45종 중 43종이 정상 격자를 채웠다). 그래서
    이 맵은 이름이 아니라 **값으로 확정했다** — 레이크 `sector_index.parquet` 의 일봉
    종가와 99거래일 전건 소수점 일치(2026-08-09). 고칠 때도 같은 방법으로 확인해라.

    `[krx_etf.source.etf_map]`(우리 id → 벤더 심볼)이 형식 선례다. 키가 우리 축이다 —
    canonical 에 실리는 `unit_id` 는 KRX 업종코드이고, KIS 코드는 질의에서만 산다
    (벤더 코드가 새어 나가면 일봉 `sector_index` 와 조인이 안 된다).
    """

    model_config = ConfigDict(extra="forbid")

    # KRX 업종코드 → KIS 지수코드. 비면 이 dataset 은 기대 집합이 0 이라 Worker 가 매
    # window 를 빈 성공으로 확정한다 — 그건 "받을 게 없다"가 아니라 배선 누락이다.
    index_map: Annotated[dict[NonBlankStr, NonBlankStr], Field(min_length=1)]

    @model_validator(mode="after")
    def _validate(self) -> MinuteSectorIndexConfig:
        # 값(KIS 코드) 중복은 **두 업종이 같은 지수를 받는다**는 뜻이다. 그 window 는
        # 정상 VALID 로 확정되고 두 unit 이 같은 값을 싣는데, 층 분해가 그 둘을 독립
        # 후보로 보면 같은 계열이 두 번 선다. 오타 한 줄이 만드는 결과치고 조용하다.
        if len(set(self.index_map.values())) != len(self.index_map):
            duplicated = sorted({v for v in self.index_map.values()
                                 if list(self.index_map.values()).count(v) > 1})
            raise ValueError(f"두 업종코드가 같은 KIS 지수코드를 가리킨다: {duplicated[:5]}")
        # 형태만 본다 — 실재는 config 로드 경로에서 알 수 없다(`MinuteUniverseConfig` 와
        # 같은 이유). 둘 다 숫자 4자리다: KRX 업종코드(`1005`·`2118`)와 KIS 지수코드
        # (`0005`·`1033`). **선행 0 이 의미를 가지므로** 정수로 접으면 안 된다.
        if bad := sorted(k for k in self.index_map if not (len(k) == 4 and k.isdigit())):
            raise ValueError(f"KRX 업종코드 형태(숫자 4자리)가 아니다: {bad[:5]}")
        if bad := sorted(v for v in self.index_map.values()
                         if not (len(v) == 4 and v.isdigit())):
            raise ValueError(f"KIS 지수코드 형태(숫자 4자리)가 아니다: {bad[:5]}")
        # ⚠️ **자리수만 보면 한 줄을 뒤집어 적어도 통과한다.** 두 코드계가 형태로 겹치기
        # 때문이다(KIS 값 대역 `1006`~`1033` ∩ KRX KOSPI 키 대역 `1005`~`1047`; 실제로
        # 14개 코드가 이 표에서 키이면서 동시에 값이다). 뒤집힌 줄은 개수도 45 그대로고
        # 값 중복도 안 나서 로드가 정상인데, canonical 의 `unit_id` 가 벤더 코드가 돼
        # **일봉 `sector_index` 와 어떤 조인에도 안 걸린다**(그 업종은 동시에 표에서
        # 사라진다). 대역 첫 자리가 **뒤바뀜은** 가른다: KRX 업종코드는 KOSPI `1xxx`·
        # KOSDAQ `2xxx` 이고, KIS 지수코드는 KOSPI 업종 `0xxx`·KOSDAQ 업종 `1xxx` 다.
        if bad := sorted(k for k in self.index_map if k[0] not in "12"):
            raise ValueError(f"KRX 업종코드 대역(1xxx·2xxx)이 아니다 — 키·값이 뒤집혔나: {bad[:5]}")
        if bad := sorted(v for v in self.index_map.values() if v[0] not in "01"):
            raise ValueError(f"KIS 지수코드 대역(0xxx·1xxx)이 아니다 — 키·값이 뒤집혔나: {bad[:5]}")
        # ⚠️ **대역만으로는 "번역을 잊은" 줄을 못 잡는다.** `"1008" = "1008"`(화학)은
        # 자리수·대역·중복을 전부 통과하는데, KIS `1008` 은 KOSDAQ 지수라 그 자리에 남의
        # 지수가 조용히 실린다 — 이 트랙을 헤매게 한 오류의 모양 그대로다. KOSPI 24행 중
        # 10행이 이 구멍에 있었다. 자기 자신을 가리키는 줄은 **번역이 안 된 것**이다:
        # 실제 표에 `키 == 값` 인 줄은 하나도 없다(KIS 는 자기 번호를 따로 쓴다).
        if bad := sorted(k for k, v in self.index_map.items() if k == v):
            raise ValueError(
                f"KRX 업종코드를 KIS 지수코드 자리에 그대로 적었다 — 번역이 빠졌나: {bad[:5]}")
        return self


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
    # 1분 판정식 v2 의 노출 회수 축(ALPHA-745) — 기준선(전일 종가) ±이 폭 안으로
    # 돌아오면 발화 금지 구간이고, 노출 중이었으면 회수 사건이 나간다. 일 단위
    # 트리거(load-price-triggers)는 이 값을 쓰지 않는다.
    revert_threshold: float = Field(default=0.01, gt=0, lt=1)
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
