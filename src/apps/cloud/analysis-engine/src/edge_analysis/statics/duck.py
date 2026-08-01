"""DuckDB 조인층 — S3(5분봉 parquet)·RDB(Postgres)·로컬 백필을 한 SQL 표면으로.

데이터가 없어도 설계는 선다: 소스가 빠지면 뷰가 안 생기고 coverage() 가
그 부재를 **보고**한다 — 조용히 빈 조인을 만드는 것이 최악이므로(P1 규율),
없는 소스를 참조하는 질의는 즉시 죽는다.

소스 우선순위: 로컬 백필 디렉터리(.tmp/causal-backfill) → RDB 터널 → S3.
경로·DSN 은 env 로 받는다. 전역 상수는 vocab 에 있다.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

RDB_DSN_ENV = "EDGE_RDB_DSN"         # 예: host=127.0.0.1 port=15432 dbname=edge user=edge password=...
BACKFILL_ENV = "CAUSAL_BACKFILL_DIR"  # 기본 .tmp/causal-backfill

# RDB 에서 끌어와 쓰는 표 — 설계 §16 간선 카탈로그의 원천.
RDB_TABLES = ("price_daily", "investor_flow_daily", "etf_holding_snapshot",
              "etf_nav_daily", "source_event", "event_argument", "instrument",
              "instrument_classification", "supply_contract_fact",
              "price_movement_trigger", "etf_contribution_observation",
              "etf_contribution_member", "event_thread", "event_thread_link")


class CausalLake:
    """한 연결 위의 뷰 집합. exists 딕셔너리가 곧 커버리지 보고서다."""

    def __init__(self, backfill_dir: str | Path | None = None,
                 rdb_dsn: str | None = None, bars_dir: str | Path | None = None):
        import duckdb
        self.con = duckdb.connect()
        # 좌표계 고정: RDB 는 UTC timestamptz, 봉은 KST naive 문자열이다.
        # 세션 TZ 를 서울로 못 박아 CAST(timestamptz AS TIMESTAMP) 가 KST naive 가
        # 되게 한다 — 머신 TZ 에 따라 τ 가 조용히 밀리는 사고를 막는다.
        self.con.execute("SET TimeZone='Asia/Seoul'")
        self.exists: dict[str, Any] = {}
        self._bars(Path(bars_dir) if bars_dir else self._default_bars())
        self._backfill(Path(backfill_dir or os.environ.get(BACKFILL_ENV, ".tmp/causal-backfill")))
        self._rdb(rdb_dsn or os.environ.get(RDB_DSN_ENV, ""))

    @staticmethod
    def _default_bars() -> Path:
        return Path(os.environ.get(BACKFILL_ENV, ".tmp/causal-backfill")) / "bars"

    def _bars(self, d: Path) -> None:
        """kr 5분봉: 종목당 parquet ({ticker}.KS.parquet, 컬럼 symbol·datetime·OHLCV)."""
        files = sorted(d.glob("*.parquet")) if d.is_dir() else []
        if not files:
            self.exists["bars_5m"] = 0
            return
        self.con.execute(
            f"CREATE VIEW bars_5m AS SELECT symbol, CAST(datetime AS TIMESTAMP) AS ts, "
            f"open, high, low, close, volume FROM read_parquet('{d.as_posix()}/*.parquet')")
        self.exists["bars_5m"] = self.con.execute("SELECT count(*) FROM bars_5m").fetchone()[0]

    def _backfill(self, d: Path) -> None:
        """로컬 백필: us_market.parquet(전일 미국장) · fx_usdkrw.parquet(환율)."""
        for name in ("us_market", "fx_usdkrw"):
            f = d / f"{name}.parquet"
            if f.is_file():
                self.con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{f.as_posix()}')")
                self.exists[name] = self.con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            else:
                self.exists[name] = 0

    def _rdb(self, dsn: str) -> None:
        """Postgres 를 postgres_scanner 로 붙인다. 터널이 없으면 부재로 보고."""
        if not dsn:
            self.exists["rdb"] = False
            return
        try:
            self.con.execute("INSTALL postgres; LOAD postgres;")
            self.con.execute(f"ATTACH '{dsn}' AS rdb (TYPE postgres, READ_ONLY)")
            for t in RDB_TABLES:
                try:
                    n = self.con.execute(f"SELECT count(*) FROM rdb.public.{t}").fetchone()[0]
                    self.exists[t] = n
                except Exception:
                    self.exists[t] = 0
            self.exists["rdb"] = True
        except Exception as e:      # 터널 죽음 = 부재 보고, 침묵 금지
            self.exists["rdb"] = f"실패: {e}"

    # ── 표면 ────────────────────────────────────────────────────────────
    def sql(self, q: str) -> list[tuple]:
        return self.con.execute(q).fetchall()

    def coverage(self) -> str:
        """프레임 노드별 데이터 유무 — 침묵하지 않는 보고서."""
        lines = ["소스               행수/상태"]
        for k, v in sorted(self.exists.items()):
            lines.append(f"{k:<18} {v}")
        return "\n".join(lines)

    def bars(self, ticker: str, day: str) -> list[tuple]:
        """(ts, close) — tree.decompose 의 입력. 심볼 규약: '005930.KS'."""
        if not self.exists.get("bars_5m"):
            raise RuntimeError("bars_5m 없음 — 백필 먼저 (coverage 참조)")
        return self.sql(
            f"SELECT ts, close FROM bars_5m WHERE symbol='{ticker}' "
            f"AND CAST(ts AS DATE) = DATE '{day}' ORDER BY ts")

    def prev_close(self, ticker: str, day: str) -> float:
        row = self.sql(
            f"SELECT close FROM bars_5m WHERE symbol='{ticker}' "
            f"AND CAST(ts AS DATE) < DATE '{day}' ORDER BY ts DESC LIMIT 1")
        if not row:
            raise RuntimeError(f"{ticker} {day} 이전 봉 없음")
        return float(row[0][0])

    def taus(self, instrument_id: str, day: str) -> list[tuple]:
        """그날(KST) 그 종목의 사건 (available_at KST naive, source_event_id)."""
        if self.exists.get("rdb") is not True:
            raise RuntimeError("RDB 부재 — coverage 참조")
        return self.sql(
            "SELECT CAST(e.available_at AS TIMESTAMP) AS t, e.source_event_id "
            "FROM rdb.public.source_event e "
            "JOIN rdb.public.event_argument a ON a.source_event_id=e.source_event_id "
            f"WHERE a.entity_id='{instrument_id}' "
            f"AND CAST(CAST(e.available_at AS TIMESTAMP) AS DATE)=DATE '{day}' "
            "GROUP BY 1,2 ORDER BY 1")


__all__ = ["CausalLake", "RDB_TABLES"]
