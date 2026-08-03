"""설정: 환경변수를 검증된 ``Settings`` 로 로드한다.

잘못되거나 빠진 값은 조용히 기본값으로 넘기지 않고 ``PipelineError`` 로 드러낸다 —
잘못 설정된 태스크가 산출물 없이 '성공'하면 안 되기 때문이다(Rule 12).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
DEFAULT_ETF_TICKER = "091160"
# deepseek-chat 은 2026-07-24 폐기 → v4. v4 계열은 thinking 기본 ON 이라
# complete_json 이 thinking:disabled 를 명시해 순수 JSON 응답을 받는다(ALPHA-469).
DEFAULT_MODEL = "deepseek-v4-flash"


class PipelineError(RuntimeError):
    """치명적 파이프라인 오류 → 비0 종료 → Step Functions 실패."""


class ReturnsNotReadyError(PipelineError):
    """당일 `price_daily` 파티션이 아직 없다(장중) — 재시도로 낫는 유일한 실패 축.

    분봉 트리거 소비자(ALPHA-719)가 이 타입만 **지연 재시도**(가시성 연장 — 15:40 배치
    뒤에 다시 본다)로 가른다. PipelineError 서브클래스라 단발 CLI 경로의 계약(비0 종료)
    은 그대로다. 장중 분해 입력을 분봉 canonical 로 바꾸는 축 결정은 ALPHA-710 이다.
    """


@dataclass(frozen=True, slots=True)
class PgConfig:
    """Cloud Event Store(Postgres) 접속 설정."""

    host: str
    port: int
    dbname: str
    user: str
    password: str | None
    schema: str


@dataclass(frozen=True, slots=True)
class Settings:
    """한 번의 실행에 필요한 검증된 설정 묶음."""

    trade_date: date
    request_id: str
    region: str
    lake_bucket: str
    etf_ticker: str
    pg: PgConfig
    deepseek_api_key: str
    deepseek_model: str
    release_bundle_version: str | None
    result_s3_prefix: str | None
    aws_profile: str | None
    # 인과 설계 하네스 사용 여부. 기본 ON.
    # OFF 로 둘 수 있게 한 이유: 인과 경로는 instrument_classification(V202607291720)을
    # 요구하고, 백필 전에는 코호트가 비어 모든 셀이 UNCERTAIN 으로 떨어진다. 그때
    # 조용히 품질이 내려가는 대신 ops 가 명시적으로 이전 경로를 고를 수 있어야 한다.
    causal_enabled: bool = True
    # 검정 에이전트의 **샌드박스 코드 실행** 사용 여부. 기본 ON — 간선마다 무엇을 어떻게
    # 잴지는 데이터를 보고 정해야 하고, 그건 코드를 쓰는 일이다. OFF 면 축약 경로(고정
    # 추정량)로 떨어진다: LLM 이 쓴 코드를 실행하는 위험을 끄고도 파이프라인이 돌아야 한다.
    causal_sandbox_enabled: bool = True

    # 분봉 트리거 단건 입력(ALPHA-709). 값이 있으면 대상 ETF·trade_date 를 그 트리거
    # 행에서 유도한다 — env 기본값으로 다른 대상을 분석하면 계보가 조용히 오염된다.
    trigger_id: str | None = None

    # 도메인 문서 저장소(S3). 비어 있으면 조회 도구를 붙이지 않는다 - 도메인 지식이
    # 없다고 설명을 멈추지 않는다. 접두사 배치는 인제스트가 만든다:
    #   index/<sector>/<industry>/chunks.parquet · docs/<sector>/<industry>/<ticker>-<n>.txt
    domain_docs_bucket: str = ""
    domain_docs_profile: str = ""
    # 메커니즘 레지스트리 뿌리(P9). 비어 있으면 소환 기록을 남기지 않는다 - 단일 사례
    # 귀속은 반복으로만 검정력을 얻으므로, 이 경로가 비면 그 축적이 통째로 없다는 뜻이다.
    causal_registry_root: str = ""
    # canonical(S3) PIT 표면. 셋이 다 있어야 붙는다 - 하나라도 비면 재무·지배구조 어휘가
    # 프롬프트에 아예 안 실리고, 그 사실이 P8 커버리지 원장에 `unavailable` 로 남는다.
    # **Cube 를 쓰지 않는 이유**: Cube 는 `*_latest` 라 시점 창이 없다 - 과거를 설명하면서
    # 나중에 정정된 값을 보게 되고, 에러 없이 조용히 미래를 본다.
    canonical_manifest: str = ""    # pit-manifest.yml 경로 (data-pipeline 이 생성)
    canonical_database: str = ""    # Glue 데이터베이스
    canonical_output: str = ""      # Athena 결과 s3:// 경로


def _flag(name: str, *, default: bool) -> bool:
    """불리언 환경변수. 오타는 fail-loud - 조용히 default 로 떨어지면 안 된다."""
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    low = raw.strip().lower()
    if low in ("1", "true", "yes", "on"):
        return True
    if low in ("0", "false", "no", "off"):
        return False
    raise PipelineError(f"invalid boolean {name}={raw!r}; expected true/false")


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def parse_trade_date(value: str | None) -> date:
    """``YYYY-MM-DD`` 파싱. 빈 값이면 오늘(KST) — 트리거 없는 날도 돌게."""
    if not value:
        return datetime.now(KST).date()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PipelineError(f"invalid trade_date {value!r}; expected YYYY-MM-DD") from exc


def _load_pg() -> PgConfig:
    schema = _env("PGSCHEMA", "public")
    # 스키마는 search_path 에 문자열로 박히므로 주입을 막는다(영숫자·밑줄만 허용).
    if schema != schema.strip() or not schema.replace("_", "").isalnum():
        raise PipelineError(f"invalid PGSCHEMA {schema!r}")
    return PgConfig(
        host=_env("PGHOST", "127.0.0.1"),
        port=int(_env("PGPORT", "5432")),
        dbname=_env("PGDATABASE", "postgres"),
        user=_env("PGUSER", "postgres"),
        password=_env("PGPASSWORD"),
        schema=schema,
    )


def load_settings(*, trade_date: str | None = None, request_id: str | None = None,
                  trigger_id: str | None = None) -> Settings:
    """환경변수와 CLI 인자로 검증된 ``Settings`` 를 만든다.

    Raises:
        PipelineError: ``DEEPSEEK_API_KEY`` 누락 또는 잘못된 ``PGSCHEMA``.
    """
    api_key = _env("DEEPSEEK_API_KEY")
    if not api_key:
        raise PipelineError("DEEPSEEK_API_KEY is not set")
    return Settings(
        trade_date=parse_trade_date(trade_date),
        request_id=request_id or f"local-{datetime.now(KST).strftime('%Y%m%dT%H%M%S')}",
        region=_env("AWS_REGION", "ap-northeast-2"),
        lake_bucket=_env("ALPHAMALE_LAKE_BUCKET", "edge-dev-pipeline-lake"),
        etf_ticker=_env("ALPHAMALE_ETF_TICKER", DEFAULT_ETF_TICKER),
        pg=_load_pg(),
        deepseek_api_key=api_key,
        deepseek_model=_env("DEEPSEEK_MODEL", DEFAULT_MODEL),
        release_bundle_version=_env("ALPHAMALE_RELEASE_BUNDLE_VERSION"),
        result_s3_prefix=_env("ALPHAMALE_RESULT_S3_PREFIX"),
        aws_profile=_env("AWS_PROFILE"),
        causal_enabled=_flag("CAUSAL_ENABLED", default=True),
        causal_sandbox_enabled=_flag("CAUSAL_SANDBOX_ENABLED", default=True),
        trigger_id=(trigger_id or None),
        domain_docs_bucket=os.environ.get("EDGE_DOMAIN_BUCKET", "").strip(),
        domain_docs_profile=os.environ.get("EDGE_AWS_PROFILE", "").strip(),
        causal_registry_root=os.environ.get("CAUSAL_REGISTRY_ROOT", "").strip(),
        canonical_manifest=os.environ.get("CANONICAL_MANIFEST", "").strip(),
        canonical_database=os.environ.get("CANONICAL_DATABASE", "").strip(),
        canonical_output=os.environ.get("CANONICAL_ATHENA_OUTPUT", "").strip(),
    )
