"""τ 사이드카 — raw 뉴스를 읽기 전용으로 파싱해 초 단위 발행시각을 곁에 둔다.

parse.bigkinds_datetime 수술(773a552)은 **앞으로의** 적재만 고친다. 기존
canonical·RDB 행은 재정제·재적재 운영 전까지 자정이다. 그 운영을 기다리지
않고, 공유 저장소를 하나도 건드리지 않고 시간 분해를 실가동하는 다리가
이 사이드카다:

    raw ndjson (S3, 읽기만) ──parse──> {article_id: 시각} 로컬 parquet
    RDB: source_event → event_evidence → document_assertion
         → document.source_document_id(=article_id)      ← duck 크로스 스토어 조인

시각 좌표: BigKinds NEWS_ID 타임스탬프는 **KST 벽시계**다. 기존 파이프라인은
이를 UTC 로 라벨해 +9h 밀린다(자정 UTC = 09:00 KST — 사건이 09:00 에 뭉친
두 번째 이유). 사이드카는 naive KST 그대로 둔다 — 봉 좌표계와 동일.

사용:  python -m edge_analysis.statics.tau_sidecar 2026-06-01 [2026-06-02 ...]
       (aws --profile work 필요. 출력: $CAUSAL_BACKFILL_DIR/tau_sidecar.parquet)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RAW_PREFIX = "s3://edge-dev-pipeline-lake/raw/source=bigkinds/dataset=stock_news/market=KR"


def _pipeline_src() -> str:
    """data_pipeline.parse 를 파일 위치 기준으로 찾는다 — 어느 체크아웃에서 돌든
    같은 리비전의 파서를 쓴다 (venv 설치본은 다른 브랜치일 수 있다)."""
    here = Path(__file__).resolve()
    for up in here.parents:
        cand = up / "data-pipeline" / "src"
        if (cand / "data_pipeline" / "parse.py").is_file():
            return str(cand)
        cand2 = up / "apps" / "cloud" / "data-pipeline" / "src"
        if (cand2 / "data_pipeline" / "parse.py").is_file():
            return str(cand2)
    raise RuntimeError("data_pipeline/parse.py 를 찾지 못했다")


def build(dates: list[str], out_dir: str | Path) -> Path:
    """날짜들의 raw 전 run_id 를 파싱해 (article_id, published_kst) parquet 를 만든다.

    같은 article_id 가 여러 run 에 오면 **가장 이른 시각**을 쓴다 — τ 는
    '시장이 처음 알 수 있던 순간'이고, 이른 쪽이 PIT 보수적이다.
    """
    sys.path.insert(0, _pipeline_src())
    from data_pipeline.parse import bigkinds_datetime, news_article_id  # noqa: E402

    best: dict[str, str] = {}
    rows_seen = 0
    with tempfile.TemporaryDirectory() as td:
        for d in dates:
            dst = Path(td) / d
            subprocess.run(
                ["aws", "--profile", "work", "s3", "cp", "--recursive", "--quiet",
                 f"{RAW_PREFIX}/published_date={d}/", str(dst)], check=True)
            for f in sorted(dst.rglob("*.ndjson")):
                # 구식 라이터가 문자열 안 개행을 이스케이프하지 않은 파티션이 있다
                # (2026-06-01 실측) — 라인 분할 대신 증분 디코드로 양쪽 다 삼킨다.
                text = f.read_text(encoding="utf-8")
                dec = json.JSONDecoder()
                i, n = 0, len(text)
                while i < n:
                    while i < n and text[i] in " \r\n\t":
                        i += 1
                    if i >= n:
                        break
                    r, i = dec.raw_decode(text, i)
                    ts = bigkinds_datetime(r)
                    if not ts or len(ts) <= 10:
                        continue                    # 자정 폴백은 사이드카에 안 넣는다
                    aid = news_article_id(r)
                    rows_seen += 1
                    if aid not in best or ts < best[aid]:
                        best[aid] = ts

    import duckdb
    out = Path(out_dir) / "tau_sidecar.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("CREATE TABLE t (article_id VARCHAR, published_kst TIMESTAMP)")
    con.executemany("INSERT INTO t VALUES (?, ?)", list(best.items()))
    con.execute(f"COPY t TO '{out.as_posix()}' (FORMAT parquet)")
    print(f"raw {rows_seen}행 → 기사 {len(best)}건 (초 단위) → {out}")
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    build(sys.argv[1:], os.environ.get("CAUSAL_BACKFILL_DIR", ".tmp/causal-backfill"))
