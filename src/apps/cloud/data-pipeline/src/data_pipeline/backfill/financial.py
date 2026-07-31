"""재무제표 백필 — **전 계정 무변형. 주요계정으로 줄이지 않는다.**

포워드 소스(`sources/dart_financial.py`)는 `fnlttSinglAcnt`(주요계정)를 쓴다. 그래서
매출액·매출원가·판관비가 없고, 사슬의 탄력성 계수(원가구조·영업레버리지)와 사건 크기의
정규화 분모(계약금액/매출)를 계산할 수 없다. 백필은 **전체 재무제표**를 받아 27열을
그대로 낸다 - bronze 는 무변형이므로 계정을 고르지 않는다.

    sj_div/sj_nm        재무제표 구분 (BS·IS·CIS·CF·SCE)
    account_id/nm/detail 계정. account_nm 에 '매출액'·'매출원가'·'판매비와관리비'가 있다
    thstrm/frmtrm/bfefrmtrm_amount  당기·전기·전전기. 누적(add_amount)·분기(q_amount) 별도
    fs_div              CFS(연결) · OFS(별도). **섞으면 원가율이 통째로 틀린다**
    reprt_code          11011(사업)·11012(반기)·11013(1분기)·11014(3분기)
    rcept_no            접수번호 - 앞 8자리가 접수일이다(PIT 의 근거, canonical 이 쓴다)

붙이는 것은 provenance 뿐이다(우리 티커·시장·수집 시각·입력 정체). 정정공시로 과거 수치가
바뀌는 문제는 raw 에서 풀지 않는다 - append 로 보존하고 SCD·PIT 판정은 canonical 소관이다.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime

from ..lake import Storage, collection_log_key, raw_financial_partition
from .hf import HfDataset, HfError
from .manifest import Manifest, sha256

logger = logging.getLogger(__name__)

SOURCE = "dartlab"                  # 포워드는 source=dart — 파티션이 겹치지 않는다
DATASET = "financial_statements"
MARKET = "KR"
FOLDER = "dart/finance"
RUN_PREFIX = "backfill-dartlab-financial"


def _rows_from_parquet(blob: bytes) -> list[dict]:
    """parquet → dict 행. **열을 고르지 않는다** - 스키마 확장은 무변형에서 온다."""
    import pyarrow.parquet as pq

    table = pq.read_table(io.BytesIO(blob))
    return table.to_pylist()


def _ndjson(rows: list[dict]) -> bytes:
    return ("\n".join(json.dumps(r, ensure_ascii=False, default=str)
                      for r in rows) + "\n").encode("utf-8")


def run_id_for(ingest_date: str, tag: str = "") -> str:
    """백필 run_id. **접두사가 격리 장치다** - 롤백은 이 파티션을 지우는 것이다."""
    stamp = ingest_date.replace("-", "")
    return f"{RUN_PREFIX}-{stamp}{('-' + tag) if tag else ''}"


def backfill_financial(storage: Storage, *, dataset: HfDataset | None = None,
                       limit: int | None = None, tickers: list[str] | None = None,
                       ingest_date: str = "", run_id: str = "",
                       key_prefix: str = "", refetch: bool = False,
                       log_every: int = 25) -> dict:
    """전 종목 재무 백필. 매니페스트로 재개하고, 끝에서 스스로 검증 가능한 상태를 남긴다.

    `key_prefix` 가 있으면 그 아래에만 쓴다(`draft/`). 승격은 접두사 이동이므로 파티션
    규약을 두 번 만들지 않는다. 포워드는 접두사가 없으니 초안이 프로덕션을 덮을 수 없다.
    """
    hf = dataset or HfDataset()
    ingest_date = ingest_date or datetime.now(UTC).date().isoformat()
    run_id = run_id or run_id_for(ingest_date)
    universe = tickers or hf.tickers(FOLDER)
    if limit:
        universe = universe[:limit]

    man = Manifest.load_or_new(
        storage, source=SOURCE, dataset=DATASET, market=MARKET, run_id=run_id,
        ingest_date=ingest_date, repo=hf.repo, revision=hf.revision, folder=FOLDER,
        prefix=key_prefix)
    prefix = raw_financial_partition(SOURCE, MARKET, ingest_date, run_id)
    if key_prefix:
        prefix = f"{key_prefix.rstrip('/')}/{prefix}"

    oids = {f.path.rsplit("/", 1)[-1][:-len(".parquet")]: f.oid
            for f in hf.files(FOLDER) if f.path.endswith(".parquet")}
    logger.info("백필 시작 run_id=%s 대상=%d prefix=%s", run_id, len(universe), prefix)

    fetched = skipped = 0
    for i, ticker in enumerate(universe, 1):
        oid = oids.get(ticker, "")
        if not refetch and man.done(ticker, oid=oid):
            skipped += 1
            continue
        try:
            blob = hf.fetch(f"{FOLDER}/{ticker}.parquet")
            rows = _rows_from_parquet(blob)
            stamp = datetime.now(UTC).isoformat()
            for r in rows:
                r["our_ticker"] = ticker
                r["market"] = MARKET
                r["fetched_at"] = stamp
                r["backfill_source"] = f"hf:{hf.repo}@{hf.revision}"
                r["backfill_oid"] = oid
            payload = _ndjson(rows)
            # **키에 내용 지문을 넣는다.** 같은 날 재개 run 에서 상류 파일이 바뀌면 고정 키는
            # 앞선 객체를 덮어쓴다 - raw 는 전부 보존이 계약이고(dedup 은 canonical 소관),
            # 덮으면 이미 canonical 로 올라간 행의 원본과 그 digest 가 사라져 재현·검증이
            # 불가능해진다. oid 를 잘라 쓰지 않는 이유: 접두사가 같은 oid 가 충돌한다.
            # 내용 주소라 같은 내용의 재실행은 같은 키 - 멱등이다.
            digest = sha256(payload)
            key = f"{prefix}/part-{ticker}-{digest[:12]}.ndjson"
            storage.put_bytes(key, payload)
            man.record(ticker, oid=oid, key=key, rows=len(rows),
                       digest=digest, bytes_out=len(payload))
            fetched += 1
        except (HfError, OSError, ValueError) as exc:
            man.fail(ticker, f"{type(exc).__name__}: {exc}")
            logger.warning("백필 실패 %s: %s", ticker, exc)
        if i % log_every == 0:
            man.save(storage)          # 중간 저장 - 중단돼도 여기서 이어진다
            logger.info("진행 %d/%d 받음=%d 건너뜀=%d 실패=%d",
                        i, len(universe), fetched, skipped, len(man.failed))

    man.close()
    man_key = man.save(storage)
    log = {"job": "backfill_financial", "source": SOURCE, "dataset": DATASET,
           "market": MARKET, "run_id": run_id, "ingest_date": ingest_date,
           "universe": len(universe), "fetched": fetched, "skipped": skipped,
           "failed": len(man.failed), "rows": man.rows,
           "manifest": man_key, "prefix": prefix,
           "input": {"repo": hf.repo, "revision": hf.revision, "folder": FOLDER}}
    key = collection_log_key(SOURCE, DATASET, ingest_date, run_id)
    storage.put_bytes(f"{key_prefix.rstrip('/') + '/' if key_prefix else ''}{key}",
                      json.dumps(log, ensure_ascii=False, indent=1).encode("utf-8"))
    logger.info("백필 종료 %s", log)
    return log
