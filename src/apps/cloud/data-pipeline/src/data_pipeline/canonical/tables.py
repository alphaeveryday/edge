"""Iceberg 테이블 등록부 — **선언이 SSOT 이고 DDL 은 생성물이다.**

DDL 을 손으로 쓰면 테이블마다 파티션 변환·압축 속성·컬럼 이름이 갈리고, 그 차이를 나중에
발견한다. 파티션이 잘못 잡힌 테이블은 되쓰는 수밖에 없으므로 규약 위반은 **선언 시점**에
걸려야 한다. `Table.validate()` 가 그 검사를 하고 테스트가 등록부 전체에 돌린다.

**append-only 로 간다.** 정체가 같으면 넣지 않고 내용이 바뀌면 새 행이 들어간다. "지금 값"과
"D 시점에 알던 값"은 뷰가 `fetched_at` 창으로 계산한다(`latest_view`·`as_of_sql`). 갱신
경로를 만들지 않으면 옛값을 잃는 경로도 없고, PIT 질의가 특수 경로가 아니라 기본이 된다.

파티션 규약은 **조회 지배축**이 정한다.

    시간 지배   `month(available_at)` + `geo`            그 날 무슨 일이 있었나
    종목 지배   `period_key`|`bsns_year` + `bucket(entity)`  이 종목 이 기간의 값
    자식        `bucket(부모키)` + `month(available_at)`  부모를 따라가되 시간축을 잃지 않는다

종목을 나열 파티션으로 두지 않는다 - 2,900개 × 기간이면 파티션이 폭발하고 Athena 가
메타데이터만 읽다 끝난다. `bucket()` 은 `WHERE entity=…` 를 버킷 하나로 줄인다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .spine import SPINE, SPINE_NAMES, TIME_PARTITIONS

# Glue DB. 존 격리를 접두사가 아니라 DB 로 한다 - Iceberg 테이블 location 은 카탈로그에
# 박히므로 같은 테이블에 접두사만 다른 데이터를 넣을 수 없다.
DB_DRAFT = "edge_lake_draft"
DB_PROD = "edge_lake"
BUCKET = "edge-dev-pipeline-lake"

_BUCKET_FN = re.compile(r"^bucket\((\d+),\s*(\w+)\)$")
_TIME_FN = re.compile(r"^(month|year|day)\((\w+)\)$")


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    note: str = ""


def spine_columns() -> tuple[Column, ...]:
    """모든 테이블이 갖는 아홉 축."""
    return tuple(Column(n, t, note) for n, t, note in SPINE)


@dataclass(frozen=True)
class Table:
    """Iceberg 테이블 하나. `partition` 은 Iceberg 변환식이다."""

    name: str
    group: str                       # canonical 도메인군 (reports·financials·estimates…)
    columns: tuple[Column, ...]
    partition: tuple[str, ...]
    identity: tuple[str, ...]        # MERGE 매칭 키. 이 조합이 같으면 같은 행이다
    note: str = ""
    props: dict[str, str] = field(default_factory=dict)

    # ── 검사 ───────────────────────────────────────────────────────────
    def validate(self) -> None:
        """규약 위반을 선언 시점에 잡는다. 적재 시점에 잡으면 이미 늦다."""
        names = self.column_names()
        if len(names) != len(set(names)):
            raise ValueError(f"{self.name}: 컬럼 이름이 겹친다")

        missing = [n for n in SPINE_NAMES if n not in names]
        if missing:
            raise ValueError(f"{self.name}: 필수 축 누락 {missing} - "
                             "이름이 갈리면 조회가 테이블마다 달라진다")

        bad = [k for k in self.identity if k not in names]
        if bad:
            raise ValueError(f"{self.name}: 정체 키가 컬럼에 없다 {bad}")
        if not self.identity:
            raise ValueError(f"{self.name}: 정체 키가 없으면 중복을 막을 수 없다")

        for p in self.partition:
            col = p
            if m := _BUCKET_FN.match(p):
                col = m.group(2)
            elif m := _TIME_FN.match(p):
                col = m.group(2)
                if p not in TIME_PARTITIONS:
                    raise ValueError(
                        f"{self.name}: 시간 파티션 {p!r} 은 허용 밖 {TIME_PARTITIONS} - "
                        "일 단위면 작은 파일이 수천 개 생긴다")
            if col not in names:
                raise ValueError(f"{self.name}: 파티션 축 {p!r} 의 컬럼이 없다")
            if col == "entity" and p == "entity":
                raise ValueError(
                    f"{self.name}: entity 를 나열 파티션으로 쓰지 않는다 - "
                    "2,900개 × 기간이면 파티션이 폭발한다. bucket() 을 쓴다")

    # ── 경로·DDL ───────────────────────────────────────────────────────
    def location(self, bucket: str = BUCKET, prefix: str = "") -> str:
        head = f"{prefix.rstrip('/')}/" if prefix else ""
        return f"s3://{bucket}/{head}canonical/{self.group}/{self.name}"

    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    def ddl(self, database: str, *, bucket: str = BUCKET, prefix: str = "") -> str:
        self.validate()
        cols = ",\n  ".join(f"{c.name} {c.type}" for c in self.columns)
        props = {"table_type": "ICEBERG", "format": "parquet",
                 "write_compression": "zstd",
                 # 대상 파일 128MB. 작은 파일이 쌓이면 조회가 메타데이터에 잡아먹힌다.
                 "write_target_data_file_size_bytes": "134217728",
                 "vacuum_max_snapshot_age_seconds": "604800", **self.props}
        tbl = ", ".join(f"'{k}'='{v}'" for k, v in props.items())
        return (f"CREATE TABLE IF NOT EXISTS {database}.{self.name} (\n  {cols}\n)\n"
                f"PARTITIONED BY ({', '.join(self.partition)})\n"
                f"LOCATION '{self.location(bucket, prefix)}'\n"
                f"TBLPROPERTIES ({tbl})")


# ── 보고서 공통 컬럼 ───────────────────────────────────────────────────
#
# 네 종류(basic·current·estimative·warning)가 **같은 스키마**를 쓴다. 다른 것은 파티션과
# 정체뿐이다. 스키마를 종류별로 따로 쓰면 같은 뜻의 컬럼이 갈리고(title vs headline),
# 종류를 넘나드는 조회가 UNION 에서 깨진다.
def report_columns(extra: tuple[Column, ...] = ()) -> tuple[Column, ...]:
    return (
        Column("report_id", "string", "<source>:<source_id> 자연키"),
        Column("content_hash", "string", "제목+본문 해시. 정체의 두 번째 축"),
        *spine_columns(),
        # 분류 계층 1·2·3
        Column("kind", "string", "basic|current|estimative|warning"),
        Column("report_type", "string", "kind 의 하위 종별"),
        Column("section", "string", "문서 내부 절. 자식 테이블의 키와 같은 어휘"),
        # 출처·신뢰도 (NATO Admiralty)
        Column("source_class", "string", "FILING|GOV|CENTRAL_BANK|WIRE|SELL_SIDE|…"),
        Column("reliability", "string", "A1~F6. 출처 등급 × 확증도"),
        Column("credibility", "string", "확증도 1~6. 확증되면 올라간다"),
        # 주제·단위
        Column("unit", "string", "ENTITY|INDUSTRY|COUNTRY|MARKET|PRODUCT|POLICY"),
        Column("cadence", "string", "SERIAL|AD_HOC. 결손 판정 입력"),
        Column("region", "string", "geo 에서 유도"),
        Column("sector", "string", "GICS 자릿수 prefix 로 롤업"),
        Column("domain", "string", "PMESII 축약"),
        Column("horizon", "string", "SPOT|NEAR|MID|LONG"),
        # 내용·권리
        Column("title", "string"),
        Column("url", "string"),
        Column("license", "string", "PUBLIC|NO_REDISTRIBUTION|INTERNAL_ONLY"),
        Column("body_ref", "string", "본문 포인터. 제한 등급은 별 버킷"),
        *extra,
    )


_REPORT_ID = ("report_id", "content_hash")

# 구조·기초. 갱신이 느리고 조회가 "이 종목 이 기간의 구조" 라서 종목 지배 파티션이다.
REPORT_BASIC = Table(
    name="report_basic", group="reports",
    note="사업보고서·10-K·산업 백서·규제 체계. 연 단위로 바뀐다",
    columns=report_columns(),
    partition=("period_key", "bucket(32, entity)"),
    identity=_REPORT_ID)

# 시황. 지배 조회가 "그 날 무슨 일이 있었나" 라서 시간 지배 파티션이다.
REPORT_CURRENT = Table(
    name="report_current", group="reports",
    note="공시·보도자료·뉴스·브리핑. 하루 수천 건",
    columns=report_columns(),
    partition=("month(available_at)", "geo"),
    identity=(*_REPORT_ID, "available_at"))

# 추정. 종목별 시계열로 읽으므로 시간 + 종목 둘 다 쓴다.
REPORT_ESTIMATIVE = Table(
    name="report_estimative", group="reports",
    note="증권사 리포트·기관 전망·가이던스. 수치는 estimates 군이 담는다",
    columns=report_columns(),
    partition=("month(available_at)", "bucket(32, entity)"),
    identity=_REPORT_ID)

# 경고. 작지만 **보존이 무한**이라 따로 둔다 - 나머지와 retention 정책이 반대다.
REPORT_WARNING = Table(
    name="report_warning", group="reports",
    note="정정공시·소송·감독조치·등급하향·감사의견. 오래된 것도 값이 있다",
    columns=report_columns((
        Column("supersedes_id", "string", "무엇을 정정했나. 없으면 빈 값"),
        Column("severity", "string", "LOW|MEDIUM|HIGH"),
    )),
    partition=("month(available_at)", "geo"),
    identity=_REPORT_ID,
    props={"vacuum_max_snapshot_age_seconds": "31536000"})   # 1년 - 함부로 지우지 않는다

# 문서 내부 절. 문서:절 = 1:N 이라 자식 테이블이다 - 부모 행에 배열로 넣으면 절 단위
# 검색이 안 되고, 임베딩을 절에 매달 수 없다.
REPORT_SECTION = Table(
    name="report_section", group="reports",
    note="사업의 내용·감사의견 같은 절. RAG 청킹의 단위",
    columns=(
        Column("report_id", "string", "부모 문서"),
        Column("section_ord", "int", "문서 내 순번"),
        Column("section", "string", "절 이름"),
        Column("section_path", "string", "계층 경로. 파이프로 이어진다"),
        Column("leaf_type", "string", "TEXT|TABLE|LIST"),
        Column("chars", "int"),
        Column("content_hash", "string"),
        Column("body_ref", "string", "본문 포인터"),
        *spine_columns(),
        Column("kind", "string", "부모의 종류를 비정규화 - 조인 없이 걸러야 한다"),
        Column("license", "string"),
    ),
    partition=("bucket(64, report_id)", "month(available_at)"),
    identity=("report_id", "section_ord"))

# 문서 ↔ 종목. 한 문서가 여러 종목을 말하므로 별 테이블이다.
REPORT_ENTITY = Table(
    name="report_entity", group="reports",
    note="문서가 언급한 종목과 역할. 노출도 계산의 입력",
    columns=(
        Column("report_id", "string"),
        Column("role", "string", "SUBJECT|MENTIONED|PEER|COUNTERPARTY"),
        Column("confidence", "double", "추출 확신도"),
        Column("mention_count", "int"),
        *spine_columns(),
        Column("kind", "string"),
    ),
    partition=("bucket(32, entity)", "month(available_at)"),
    identity=("report_id", "entity", "role"))


# ── 재무 ───────────────────────────────────────────────────────────────
#
# 원본은 한 행에 당기·전기·전전기 금액이 함께 있다(6개 금액 열). 그대로 두면 **다른 공시의
# 당기와 전기를 맞대는 사고**가 나고, 그 사고는 조용하다 - 숫자가 나오기 때문이다.
# canonical 에서 **금액 하나가 한 행**이 되게 펴고 기간·성격을 컬럼으로 못박는다.
STATEMENT_LINE = Table(
    name="statement_line", group="financials",
    note="재무제표 계정 한 줄 = 금액 하나. 기간·성격이 컬럼으로 고정된다",
    columns=(
        Column("corp_code", "string", "DART 고유번호"),
        Column("corp_name", "string"),
        Column("fs_div", "string", "CFS(연결)·OFS(별도). **섞으면 원가율이 통째로 틀린다**"),
        Column("fs_nm", "string"),
        Column("sj_div", "string", "BS·IS·CIS·CF·SCE"),
        Column("sj_nm", "string"),
        Column("account_id", "string", "IFRS 태그 또는 -표준계정미사용-"),
        Column("account_nm", "string"),
        Column("account_detail", "string", "SCE 의 축. 파이프로 계층이 이어진다"),
        Column("ord", "int", "공시 내 순번. 같은 계정이 여러 줄일 때 이것이 가른다"),
        Column("period_kind", "string", "THSTRM|FRMTRM|BFEFRMTRM - 원본 열이 가리킨 기간"),
        Column("amount_kind", "string", "POINT|CUMULATIVE|QUARTER"),
        Column("period_label", "string", "제 194 기 같은 원문 표기"),
        # **bigint 가 아니다.** 처음 bigint 로 잡았더니 `기본주당이익(손실) = 0.33` 이
        # 파싱에 실패했다(실측 2건). double 도 아니다 - 재무 수치를 부동소수로 담으면
        # 합계가 미세하게 어긋나고 그 차이를 사후에 설명할 수 없다.
        Column("amount", "decimal(38,6)"),
        Column("amount_text", "string", "원문. 파싱 실패를 드러내기 위해 남긴다"),
        Column("currency", "string"),
        Column("bsns_year", "string",
               "파티션 축. period_key 와 겹치지만 파티션 값이 짧아야 카탈로그가 가볍다"),
        Column("reprt_code", "string", "11011 사업·11012 반기·11013 1Q·11014 3Q"),
        Column("reprt_nm", "string"),
        Column("rcept_no", "string", "접수번호 = 문서키"),
        # **금액 지문.** 정정공시로 같은 (접수번호·구분·순번·기간·성격) 의 금액만 바뀌면
        # 정체가 같아 `WHEN NOT MATCHED` 가 건너뛴다 - raw 는 새로 받았는데 canonical 은
        # 옛 금액을 들고 있게 된다. 지문을 정체에 넣어 **정정본을 새 행**으로 만든다
        # (append-only). reports 레인이 `content_hash` 로 하는 것과 같은 규약이고,
        # `latest_view`·`as_of_sql` 은 정체에서 이 축을 빼므로 "지금 값"은 그대로 하나다.
        Column("content_hash", "string", "금액 내용 지문. 정정으로 값이 바뀌면 새 행이다"),
        *spine_columns(),
    ),
    partition=("bsns_year", "bucket(32, entity)"),
    identity=("rcept_no", "fs_div", "sj_div", "ord", "period_kind", "amount_kind",
              "content_hash"))

# 공시 단위 메타. "이 접수번호에 무엇이 들어 있었나" 를 한 행으로 - 계정 단위 테이블을
# 훑지 않고 결손을 판정하려면 이 요약이 필요하다.
FILING_META = Table(
    name="filing_meta", group="financials",
    note="공시 하나 = 한 행. 어떤 재무제표 구분이 있었고 몇 계정이었나",
    columns=(
        Column("rcept_no", "string"),
        Column("corp_code", "string"),
        Column("corp_name", "string"),
        Column("bsns_year", "string"),
        Column("reprt_code", "string"),
        Column("fs_divs", "string", "쉼표로 이은 목록. CFS,OFS"),
        Column("sj_divs", "string", "BS,IS,CIS,CF,SCE"),
        Column("account_count", "int"),
        Column("line_count", "int", "펴진 뒤 행 수"),
        Column("unparsed_count", "int", "금액 파싱 실패 수. 0 이 아니면 봐야 한다"),
        *spine_columns(),
    ),
    partition=("month(available_at)", "bucket(32, entity)"),
    identity=("rcept_no",))


# ── 컨센서스·추정치 ────────────────────────────────────────────────────
#
# 리포트 **문서**는 reports 군이고, 그 안의 **수치**는 여기다. 둘을 한 테이블에 두면 한
# 리포트가 여러 지표·여러 기간을 담을 때 행이 뭉개진다. `report_id` 로 이어 붙인다.
ESTIMATE_LINE = Table(
    name="estimate_line", group="estimates",
    note="증권사 한 곳의 추정치 한 개 = 한 행",
    columns=(
        Column("report_id", "string", "출처 문서. 없으면 빈 값"),
        Column("broker", "string", "증권사·기관"),
        Column("analyst", "string"),
        Column("metric", "string", "TARGET_PRICE|EPS|OPERATING_PROFIT|REVENUE|RATING"),
        Column("value", "decimal(38,6)"),
        Column("value_text", "string", "원문. RATING 처럼 수치가 아닌 것도 있다"),
        Column("currency", "string"),
        Column("prev_value", "decimal(38,6)", "직전 추정. 상향·하향 판정의 근거"),
        Column("license", "string", "셀사이드는 보통 NO_REDISTRIBUTION"),
        *spine_columns(),
    ),
    partition=("month(available_at)", "bucket(32, entity)"),
    identity=("source", "entity", "broker", "metric", "period_key", "available_at"))

# 집계된 컨센서스. 개별 추정과 분리하는 이유는 **집계 시점이 다르기** 때문이다 -
# 같은 기간의 컨센서스가 매일 바뀐다. append-only 라 그 궤적이 남는다.
CONSENSUS_POINT = Table(
    name="consensus_point", group="estimates",
    note="한 시점의 집계 컨센서스. 매일 쌓여 궤적이 된다",
    columns=(
        Column("metric", "string"),
        Column("mean", "decimal(38,6)"),
        Column("median", "decimal(38,6)"),
        Column("high", "decimal(38,6)"),
        Column("low", "decimal(38,6)"),
        Column("stdev", "decimal(38,6)", "분산이 곧 불확실성. 사슬의 구간 폭에 쓰인다"),
        Column("n_estimates", "int"),
        Column("currency", "string"),
        Column("license", "string"),
        *spine_columns(),
    ),
    partition=("month(available_at)", "bucket(32, entity)"),
    identity=("source", "entity", "metric", "period_key", "available_at"))


# ── 기타 기업 자료 ─────────────────────────────────────────────────────
SHAREHOLDER_STAKE = Table(
    name="shareholder_stake", group="ownership",
    note="주요주주·임원 지분. 변동 자체가 사건이다",
    columns=(
        Column("holder_name", "string"),
        Column("holder_type", "string", "MAJOR|EXECUTIVE|INSTITUTION|FOREIGN"),
        Column("shares", "bigint"),
        Column("ratio", "decimal(18,6)", "지분율 %"),
        Column("prev_ratio", "decimal(18,6)"),
        Column("reason", "string", "변동 사유"),
        Column("rcept_no", "string"),
        *spine_columns(),
    ),
    partition=("month(available_at)", "bucket(32, entity)"),
    identity=("source", "entity", "holder_name", "available_at", "rcept_no"))

ENTITY_MASTER = Table(
    name="entity_master", group="reference",
    note="종목 마스터. GICS·상장상태·이름의 시점 이력",
    columns=(
        Column("name", "string"),
        Column("name_en", "string"),
        Column("corp_code", "string"),
        Column("isin", "string"),
        Column("market_venue", "string", "KOSPI|KOSDAQ|NYSE|NASDAQ"),
        Column("gics_sector", "string", "2자리"),
        Column("gics_industry_group", "string", "4자리"),
        Column("gics_industry", "string", "6자리"),
        Column("listed", "boolean"),
        Column("valid_from", "date", "이 행이 유효해지는 날. 이름·분류가 바뀌면 새 행"),
        *spine_columns(),
    ),
    partition=("geo",),
    identity=("entity", "geo", "valid_from"))


# ── 등록부 ─────────────────────────────────────────────────────────────
TABLES: tuple[Table, ...] = (
    REPORT_BASIC, REPORT_CURRENT, REPORT_ESTIMATIVE, REPORT_WARNING,
    REPORT_SECTION, REPORT_ENTITY,
    STATEMENT_LINE, FILING_META,
    ESTIMATE_LINE, CONSENSUS_POINT,
    SHAREHOLDER_STAKE, ENTITY_MASTER,
)
BY_NAME = {t.name: t for t in TABLES}
GROUPS = tuple(dict.fromkeys(t.group for t in TABLES))


def latest_view(table: Table, database: str, *, key: tuple[str, ...] = ()) -> str:
    """"지금 값" 뷰 — 정체 기준 마지막 관측.

    append-only 라 같은 대상에 내용이 다른 행이 여럿 있을 수 있다(정정·수정). 그 중 무엇이
    현재인지는 저장이 아니라 **조회가** 판정한다 - 저장이 판정하면 옛값을 지우는 경로가
    필요해지고, 그 경로가 PIT 를 깬다.
    """
    keys = key or tuple(k for k in table.identity if k != "content_hash")
    cols = ", ".join(table.column_names())
    return (f"CREATE OR REPLACE VIEW {database}.{table.name}_latest AS\n"
            f"SELECT {cols} FROM (\n"
            f"  SELECT {cols}, row_number() OVER (\n"
            f"    PARTITION BY {', '.join(keys)} ORDER BY fetched_at DESC) AS rn\n"
            f"  FROM {database}.{table.name}\n) WHERE rn = 1")


def as_of_sql(table: Table, database: str, as_of: str,
              *, key: tuple[str, ...] = ()) -> str:
    """PIT 질의 — **그 시각까지 우리가 알던 것만.**

    `fetched_at <= as_of` 로 창을 닫고 그 안의 마지막 관측을 고른다. 사후에 들어온 정정본이
    섞이면 조용히 미래를 본다 - 백테스트가 실제보다 좋게 나오고, 그 원인을 찾기 어렵다.
    """
    keys = key or tuple(k for k in table.identity if k != "content_hash")
    cols = ", ".join(table.column_names())
    return (f"SELECT {cols} FROM (\n"
            f"  SELECT {cols}, row_number() OVER (\n"
            f"    PARTITION BY {', '.join(keys)} ORDER BY fetched_at DESC) AS rn\n"
            f"  FROM {database}.{table.name}\n"
            f"  WHERE fetched_at <= TIMESTAMP '{as_of}'\n) WHERE rn = 1")
