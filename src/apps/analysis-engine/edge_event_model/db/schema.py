"""Single source of truth for the unified prediction/debug log schema.

One table, ``daily_prediction``, holds everything needed to read AND debug a
prediction: per-layer outputs, ground truth, prediction, error, and failure
fields (no separate fail-log table). English column names with Korean comments;
this module emits matching DDL + comments for both SQLite and PostgreSQL.
"""
from __future__ import annotations

from datetime import datetime, timezone

TABLE = "daily_prediction"
PRIMARY_KEY = ("trade_date", "market", "asset_code")

# (name, sqlite_type, pg_type, korean_comment)
COLUMNS: tuple[tuple[str, str, str, str], ...] = (
    # --- 식별 / 실행 ---
    ("trade_date", "TEXT", "date", "예측 기준일"),
    ("market", "TEXT", "varchar", "시장 (US/KR)"),
    ("asset_code", "TEXT", "varchar", "종목코드(티커)"),
    ("sector", "TEXT", "varchar", "섹터"),
    ("status", "TEXT", "varchar", "처리상태: ok / skipped / failed"),
    ("split", "TEXT", "varchar", "데이터 분할: train / validation / test (라이브는 NULL)"),
    # --- 입력 (그날 확정 데이터) ---
    ("news_count", "INTEGER", "integer", "당일 사용 뉴스 수"),
    ("news_ids", "TEXT", "jsonb", "사용된 뉴스 article_id 목록"),
    ("input_factor_values", "TEXT", "jsonb", "당일 FF5 팩터값 벡터 (factor_vector)"),
    ("prev_close_price", "REAL", "double precision", "전일 종가"),
    # --- 1층: FF5 선형회귀 ---
    ("layer1_ff5_normal_return", "REAL", "double precision", "[1층 FF5] 정상수익률 예측"),
    ("layer1_ff5_alpha", "REAL", "double precision", "[1층 FF5] 절편 alpha"),
    ("layer1_ff5_importance_betas", "TEXT", "jsonb", "[1층 FF5] 중요도 베타 벡터 (importance_vector)"),
    ("layer1_ff5_r2", "REAL", "double precision", "[1층 FF5] 설명력 R^2"),
    ("layer1_ff5_residual_std", "REAL", "double precision", "[1층 FF5] 잔차 표준편차"),
    # --- 2층: 뉴스 NN ---
    ("layer2_news_abnormal_return", "REAL", "double precision", "[2층 NN] 비정상수익률 추정"),
    ("layer2_news_uncertainty", "REAL", "double precision", "[2층 NN] 예측 불확실성 sigma"),
    # --- 3층: 최종 선형회귀 ---
    ("layer3_final_abnormal_return", "REAL", "double precision", "[3층 최종회귀] 예측 비정상수익률"),
    # --- 최종 예측 ---
    ("predicted_return", "REAL", "double precision", "최종 예측 수익률 (정상+비정상)"),
    ("predicted_close_price", "REAL", "double precision", "예측 종가"),
    ("predicted_high_price", "REAL", "double precision", "예측 고가"),
    ("predicted_direction", "INTEGER", "integer", "예측 방향 (+1 상승 / -1 하락)"),
    ("close_confidence", "REAL", "double precision", "종가 컨피던스 [0,1]"),
    ("high_confidence", "REAL", "double precision", "고가 컨피던스 [0,1]"),
    # --- 정답 (사후 실현값) ---
    ("actual_return", "REAL", "double precision", "실제 수익률 (정답)"),
    ("actual_close_price", "REAL", "double precision", "실제 종가 (정답)"),
    ("actual_high_price", "REAL", "double precision", "실제 고가 (정답)"),
    ("actual_abnormal_return", "REAL", "double precision", "실현 비정상수익률 (정답)"),
    # --- 오차 / 판정 ---
    ("return_error", "REAL", "double precision", "오차: 예측수익률 - 실제수익률"),
    ("close_price_error", "REAL", "double precision", "오차: 예측종가 - 실제종가"),
    ("is_event", "INTEGER", "boolean", "이벤트 후보 (|비정상수익률| >= 5%)"),
    ("calibration_pass", "INTEGER", "boolean", "캘리브레이션 통과 (오차<2% & 방향일치)"),
    # --- 디버그 / 실패 ---
    ("error_code", "TEXT", "varchar", "실패/스킵 코드 (성공 시 NULL)"),
    ("error_message", "TEXT", "text", "실패/스킵 사람이 읽는 메시지"),
    ("debug_payload", "TEXT", "jsonb", "디버그용 원입력 스냅샷"),
    # --- 메타 ---
    ("model_version", "TEXT", "varchar", "모델 버전"),
    ("embed_model", "TEXT", "varchar", "임베딩 모델"),
    ("ff5_available", "INTEGER", "boolean", "당일 FF5 팩터 가용 여부"),
    ("created_at", "TEXT", "timestamptz", "행 생성 시각 (UTC)"),
)

COLUMN_NAMES: tuple[str, ...] = tuple(c[0] for c in COLUMNS)
JSON_COLUMNS: frozenset[str] = frozenset({"news_ids", "input_factor_values", "layer1_ff5_importance_betas", "debug_payload"})
BOOL_COLUMNS: frozenset[str] = frozenset({"is_event", "calibration_pass", "ff5_available"})
_KO: dict[str, str] = {c[0]: c[3] for c in COLUMNS}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sqlite_ddl(table: str = TABLE) -> str:
    body = []
    for name, sqlite_type, _pg, comment in COLUMNS:
        notnull = " NOT NULL" if name in PRIMARY_KEY else ""
        body.append(f"    {name:<30} {sqlite_type}{notnull},  -- {comment}")
    body.append(f"    PRIMARY KEY ({', '.join(PRIMARY_KEY)})")
    return f"CREATE TABLE IF NOT EXISTS {table} (\n" + "\n".join(body) + "\n)"


def pg_ddl(table: str = TABLE) -> str:
    cols = []
    for name, _sqlite, pg_type, _comment in COLUMNS:
        notnull = " NOT NULL" if name in PRIMARY_KEY else ""
        cols.append(f"    {name} {pg_type}{notnull}")
    cols.append(f"    PRIMARY KEY ({', '.join(PRIMARY_KEY)})")
    return f"CREATE TABLE IF NOT EXISTS {table} (\n" + ",\n".join(cols) + "\n)"


def pg_comment_sql(table: str = TABLE) -> list[str]:
    out = [f"COMMENT ON TABLE {table} IS '일자별 종목 예측/디버그 통합 로그'"]
    for name, _s, _p, comment in COLUMNS:
        escaped = comment.replace("'", "''")
        out.append(f"COMMENT ON COLUMN {table}.{name} IS '{escaped}'")
    return out


def upsert_sql(table: str = TABLE, dialect: str = "sqlite") -> str:
    placeholder = "?" if dialect == "sqlite" else "%s"
    cols = ", ".join(COLUMN_NAMES)
    vals = ", ".join(placeholder for _ in COLUMN_NAMES)
    updates = ", ".join(f"{c}=excluded.{c}" for c in COLUMN_NAMES if c not in PRIMARY_KEY)
    return (
        f"INSERT INTO {table} ({cols}) VALUES ({vals}) "
        f"ON CONFLICT ({', '.join(PRIMARY_KEY)}) DO UPDATE SET {updates}"
    )


def data_dictionary_md(table: str = TABLE) -> str:
    rows = ["| column | type | 설명 |", "|---|---|---|"]
    for name, sqlite_type, _pg, comment in COLUMNS:
        rows.append(f"| `{name}` | {sqlite_type} | {comment} |")
    return f"### {table}\n\n" + "\n".join(rows)
