"""보고서 분류체계 — **정보 분석의 표준 축을 우리 시스템에 맞춘 것.**

광의의 보고서를 쌓으면 곧 "무엇을 어디서 찾나"가 문제가 된다. 산업 하나로 자르면 국가·
출처·신뢰도가 사라지고, 축을 임의로 늘리면 조회 규칙이 사람의 기억에 남는다. 그래서
이미 검증된 체계를 가져온다 - 정보기관이 수십 년간 같은 문제를 풀어 온 방식이다.

    KIND          미 정보공동체 보고서 구분(basic·current·estimative·warning)
                  **변화 속도와 역할이 다르다.** basic 은 구조를, current 는 사건을,
                  estimative 는 추정의 앵커를, warning 은 반증조건을 준다
    SOURCE_CLASS  수집 출처 구분(OSINT 계열을 우리 도메인으로 특화)
    RELIABILITY   NATO Admiralty Code (STANAG 2511): 출처 A-F × 내용 1-6
                  같은 수치라도 A1 과 D4 는 사슬에서 구간 폭이 달라야 한다
    DOMAIN        PMESII-PT 를 금융 분석에 맞게 축약
    HORIZON       추정의 시간 지평 - 지속 기간 가정의 근거가 된다

`kind` 를 raw 파티션 축으로 쓰지 않는다. 분류 규칙은 바뀌고, 바뀔 때 이미 쌓인 파티션을
옮겨야 하는 설계는 백필을 못 한다. **raw 에서는 레코드 컬럼**이고, 종류로 테이블을 가르는
것은 canonical 소관이다.

신뢰도는 사람이 붙이지 않는다. 출처 유형이 등급(A-F)을 주고 확증도(1-6)는 다른 출처와의
일치로 계산한다 - 사람이 붙이면 스케일이 안 된다. 이 모듈은 등급의 **기본값**만 정한다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# ── 최상위: 보고서 종류 ────────────────────────────────────────────────
KIND_BASIC = "basic"              # 구조·기초. 갱신 느림 (사업보고서·산업 백서·규제 체계)
KIND_CURRENT = "current"          # 시황. 갱신 빠름 (공시·뉴스·보도자료)
KIND_ESTIMATIVE = "estimative"    # 추정 (애널리스트 리포트·컨센서스·기관 전망)
KIND_WARNING = "warning"          # 경고 (정정공시·소송·규제조치·등급 하향)
KINDS = (KIND_BASIC, KIND_CURRENT, KIND_ESTIMATIVE, KIND_WARNING)

# ── 2단: 문서 종별 (kind 의 하위) ──────────────────────────────────────
#
# 최상위만 두면 `current` 안에서 공시와 보도자료와 뉴스가 한 덩어리가 된다. 그 셋은
# 신뢰도·갱신 주기·파싱 방식이 다르고, 무엇보다 **사슬에서 쓰이는 자리가 다르다** -
# 공시는 사건 그 자체이고 보도자료는 정책 경로의 입력이며 뉴스는 반응의 관측이다.
#
# IC 의 보고서 계열을 우리 도메인으로 옮긴 것이다: basic 은 요람·편제(World Factbook·
# order of battle)에 해당하는 구조 서술, current 는 일일 브리핑 계열, estimative 는
# NIE(국가정보판단) 계열, warning 은 I&W(지표·경보) 계열.
REPORT_TYPES: dict[str, tuple[str, ...]] = {
    KIND_BASIC: (
        "ANNUAL_REPORT",      # 사업보고서·10-K 본문 (구조·제품·원재료·고객)
        "INDUSTRY_PRIMER",    # 산업 백서·밸류체인 지도
        "REGULATORY_FRAME",   # 규제 체계·제도 해설
        "ENTITY_PROFILE",     # 기업 프로필·지배구조
        "COUNTRY_HANDBOOK",   # 국가 요람 (거시 구조·정책 체계)
        "STATISTICAL_SERIES",  # 통계 계열 해설 (지표 정의·산출 방법)
    ),
    KIND_CURRENT: (
        "FILING_EVENT",       # 공시 (사건 그 자체)
        "PRESS_RELEASE",      # 보도자료 (정책·기업 발표)
        "NEWS_ARTICLE",       # 언론 기사
        "MARKET_BRIEF",       # 시황 브리핑·일일 요약
        "TRANSCRIPT",         # 컨퍼런스콜·기자회견 녹취
    ),
    KIND_ESTIMATIVE: (
        "SELL_SIDE_REPORT",   # 증권사 리포트 (목표주가·추정치)
        "CONSENSUS_SNAPSHOT",  # 컨센서스 집계 스냅샷
        "INSTITUTIONAL_OUTLOOK",  # 기관 전망 (IMF·OECD·한은)
        "SCENARIO",           # 시나리오·민감도
        "GUIDANCE",           # 기업 자체 가이던스
    ),
    KIND_WARNING: (
        "AMENDMENT",          # 정정공시 (**PIT 의 핵심**)
        "LITIGATION",         # 소송·분쟁
        "ENFORCEMENT",        # 규제·감독 조치
        "RATING_ACTION",      # 신용등급 변경
        "AUDIT_FLAG",         # 감사의견·강조사항·핵심감사사항
        "INDICATOR_TRIP",     # 지표 경보 (I&W - 문턱 이탈)
    ),
}
ALL_REPORT_TYPES = tuple(t for ts in REPORT_TYPES.values() for t in ts)

# ── 분석 단위 ──────────────────────────────────────────────────────────
# 같은 문서가 무엇에 관한 것인가. IC 가 표적 수준을 구분하는 자리다 - 이 축이 없으면
# "삼성전자 리포트"와 "반도체 산업 리포트"가 같은 서랍에 들어가 조회가 뭉개진다.
UNITS = ("ENTITY", "INDUSTRY", "COUNTRY", "MARKET", "PRODUCT", "POLICY")

# ── 발간 주기 ──────────────────────────────────────────────────────────
# 정기물과 비정기물은 결손 판정이 다르다. 정기물이 빠지면 결손이고, 비정기물은 없는 것이
# 정상이다 - 백필 검증이 이 축 없이는 "빈 날"과 "누락"을 못 가른다.
CADENCES = ("SERIAL", "AD_HOC")

# ── 출처 구분 ──────────────────────────────────────────────────────────
# `origin_feeds.json` 의 `class` 를 이어받아 확장한다 - 이미 돌던 분류를 버리지 않는다.
SOURCE_CLASSES = (
    "FILING",         # 공시 (DART·KIND·EDGAR)
    "GOV",            # 정부 (korea.kr·whitehouse.gov·부처)
    "CENTRAL_BANK",   # 중앙은행 (한국은행·Fed)
    "REGULATOR",      # 감독기관 (금감원·SEC·공정위)
    "WIRE",           # 통신사 (연합·Reuters)
    "PRESS",          # 언론
    "SELL_SIDE",      # 증권사 리포트
    "IR",             # 기업 IR·보도자료
    "MULTILATERAL",   # 국제기구 (IMF·OECD·IEA)
    "ACADEMIC",       # 학술·연구기관
    "INDUSTRY_BODY",  # 협회·단체
)

# 출처 유형 → Admiralty 출처 등급 기본값. **문서 하나하나 사람이 매기지 않는다.**
#   A 완전 신뢰 · B 대체로 신뢰 · C 상당히 신뢰 · D 대체로 불신 · E 불신 · F 판단 불가
SOURCE_GRADE = {
    "FILING": "A", "CENTRAL_BANK": "A", "REGULATOR": "A",
    "GOV": "B", "MULTILATERAL": "B", "SELL_SIDE": "B", "ACADEMIC": "B",
    "WIRE": "C", "IR": "C", "INDUSTRY_BODY": "C",
    "PRESS": "D",
}
# 내용 확증도. 1 다른 출처로 확증 · 2 개연성 높음 · 3 개연성 있음 · 4 의심 · 5 비개연 ·
# 6 판단 불가. 기본은 6(미확증)이고, 다른 출처와의 일치가 확인되면 canonical 이 올린다 -
# 수집 시점에는 확증 여부를 알 수 없으므로 여기서 낙관적으로 매기지 않는다.
CREDIBILITY_UNASSESSED = "6"

# ── 주제 (PMESII-PT 축약) ──────────────────────────────────────────────
DOMAINS = ("POLITICAL", "ECONOMIC", "MILITARY", "SOCIAL",
           "INFORMATION", "INFRASTRUCTURE", "CORPORATE")

# ── 시간 지평 ──────────────────────────────────────────────────────────
HORIZONS = ("SPOT", "NEAR", "MID", "LONG")     # 즉시 · ≤3M · ≤1Y · 그 이상


# ── 지리 계층 ──────────────────────────────────────────────────────────
# ISO 3166 국가 위에 권역을 얹는다. Athena 에서 권역 롤업을 하려면 국가만으로는 매번
# CASE 문을 쓰게 되고, 그 규칙이 질의마다 갈린다.
REGION_OF = {"KR": "APAC", "JP": "APAC", "CN": "APAC", "TW": "APAC",
             "US": "AMER", "CA": "AMER", "DE": "EMEA", "GB": "EMEA",
             "GLOBAL": "GLOBAL"}


@dataclass(frozen=True)
class ReportClass:
    """보고서 한 종류의 분류 좌표. **수집기가 선언하고 코드가 검사한다.**

    계층이 셋이다. 문서 종별(`kind` → `report_type` → `section`), 주제(`geo` → `region`
    과 GICS `sector` → `industry`), 그리고 분석 단위(`unit`). 평평한 태그 묶음이 아니라
    상하 관계가 있어야 롤업 조회가 규칙으로 돌고 사람의 기억에 남지 않는다.
    """

    kind: str
    source_class: str
    report_type: str = ""            # kind 의 하위 종별. 비면 kind 만으로 뭉개진다
    section: str = ""                # 문서 내부 절 (사업의 내용·감사의견…). 선택
    unit: str = "ENTITY"             # 분석 단위
    cadence: str = "AD_HOC"          # 정기물인가
    geo: str = "KR"                  # ISO 3166 또는 GLOBAL
    sector: str = ""                 # GICS 코드 (2·4·6자리). 기업·산업 문서에만
    domain: str = "ECONOMIC"
    horizon: str = "SPOT"
    license: str = "PUBLIC"          # PUBLIC | NO_REDISTRIBUTION | INTERNAL_ONLY
    credibility: str = CREDIBILITY_UNASSESSED

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind={self.kind!r} 는 {KINDS} 밖이다")
        # **계층 검사.** 종별이 상위 종류에 속하지 않으면 조용히 잘못 분류된다 -
        # 어휘 검사만으로는 `current` 에 `AMENDMENT` 가 붙는 것을 못 막는다.
        if self.report_type and self.report_type not in REPORT_TYPES[self.kind]:
            raise ValueError(
                f"report_type={self.report_type!r} 는 kind={self.kind!r} 의 하위가 아니다 "
                f"(허용: {REPORT_TYPES[self.kind]})")
        if self.source_class not in SOURCE_CLASSES:
            raise ValueError(f"source_class={self.source_class!r} 는 어휘 밖이다")
        if self.unit not in UNITS:
            raise ValueError(f"unit={self.unit!r} 는 {UNITS} 밖이다")
        if self.cadence not in CADENCES:
            raise ValueError(f"cadence={self.cadence!r} 는 {CADENCES} 밖이다")
        if self.domain not in DOMAINS:
            raise ValueError(f"domain={self.domain!r} 는 {DOMAINS} 밖이다")
        if self.horizon not in HORIZONS:
            raise ValueError(f"horizon={self.horizon!r} 는 {HORIZONS} 밖이다")

    @property
    def reliability(self) -> str:
        """Admiralty 코드. 출처 등급 + 확증도 - **둘을 분리해 붙이는 것이 요점이다.**"""
        return f"{SOURCE_GRADE.get(self.source_class, 'F')}{self.credibility}"

    @property
    def region(self) -> str:
        """권역. 국가에서 유도한다 - 수집기가 따로 선언하면 값이 갈린다."""
        return REGION_OF.get(self.geo, "OTHER")

    def as_columns(self) -> dict[str, str]:
        """레코드에 붙는 분류 컬럼. 파티션이 아니라 컬럼이다."""
        return {**asdict(self), "reliability": self.reliability, "region": self.region}
