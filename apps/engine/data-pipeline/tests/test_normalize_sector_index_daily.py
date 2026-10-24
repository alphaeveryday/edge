"""normalize_sector_index_daily 테스트 — 업종지수 일봉 고유 규칙만 본다.

병합·manifest·CAS·재시도 장치는 NAV 정제와 같은 코드라 test_normalize_etf_nav 가 덮는다.
"""

import json

from data_pipeline.lake import (
    LocalStorage,
    canonical_run_manifest_key,
    canonical_sector_index_daily_partition,
    raw_sector_index_daily_partition,
)
from data_pipeline.steps import normalize_etf_nav, normalize_sector_index_daily


def _row(code, day, close="4636.80", *, open_=None, high="4700.00", low="4600.00",
         fetched_at="2026-10-08T11:30:00+00:00"):
    return {
        "stck_bsop_date": day, "bstp_nmix_prpr": close,
        "bstp_nmix_oprc": open_ if open_ is not None else close,
        "bstp_nmix_hgpr": high, "bstp_nmix_lwpr": low, "acml_vol": "123",
        "acml_tr_pbmn": "456", "mod_yn": "N",
        "index_code": code, "market": "KR", "kis_symbol": "0005", "fetched_at": fetched_at,
    }


def _write_raw(storage, run_id, rows, ingest_date="2026-10-08"):
    key = f"{raw_sector_index_daily_partition('kis', 'KR', ingest_date, run_id)}/part-00000.ndjson"
    storage.put_bytes(key, "".join(json.dumps(r) + "\n" for r in rows).encode())


def _canonical(storage, trade_date):
    key = f"{canonical_sector_index_daily_partition('KR', trade_date)}/part-00000.parquet"
    return normalize_etf_nav._read_parquet_rows(storage.get_bytes(key))


def test_KRX_업종코드_행으로_OHLC만_싣는다(tmp_path):
    # WHY: 분석 백필 `sector_index`(trade_date·code·close)를 대체할 표다 — 행 키는 KRX 코드여야
    # 조인이 되고, 단위를 확정하지 못한 거래량·거래대금과 의미 없는 통화는 싣지 않는다.
    storage = LocalStorage(tmp_path)
    _write_raw(storage, "R1", [_row("1005", "20261008", open_="4650.00")])

    assert normalize_sector_index_daily.run(storage, "N1", "R1") == 0

    [row] = _canonical(storage, "2026-10-08")
    assert row == {
        "market": "KR", "code": "1005", "trade_date": "2026-10-08",
        "open": 4650.0, "high": 4700.0, "low": 4600.0, "close": 4636.8,
        "source_vendor": "kis", "fetched_at": "2026-10-08T11:30:00+00:00",
    }
    manifest = json.loads(storage.get_bytes(
        canonical_run_manifest_key("sector_index_daily", "N1")))
    assert manifest["producer"] == "normalize_sector_index_daily"
    assert manifest["canonical_partitions"][0]["winner_ids"] == [{"code": "1005"}]


def test_재실행은_앞선_거래일을_지우지_않고_늦게_받은_정정이_이긴다(tmp_path):
    # WHY(ALPHA-835 합격 기준): 통째 덮어쓰기는 앞선 행을 지운 전례가 있다(ALPHA-828).
    # 소급 백필 뒤 정기 런이 돌아도 07월 행은 남아야 하고, 같은 날의 정정은 반영돼야 한다.
    storage = LocalStorage(tmp_path)
    _write_raw(storage, "R1", [_row("1005", "20260701"), _row("1005", "20261007", "100.00",
                                                                  high="101.00", low="99.00")])
    assert normalize_sector_index_daily.run(storage, "N1", "R1") == 0

    _write_raw(storage, "R2", [_row("1005", "20261007", "200.00", high="201.00", low="199.00",
                                    fetched_at="2026-10-09T11:30:00+00:00")],
               ingest_date="2026-10-09")
    assert normalize_sector_index_daily.run(storage, "N2", "R2") == 0

    assert [r["close"] for r in _canonical(storage, "2026-07-01")] == [4636.8]
    assert [r["close"] for r in _canonical(storage, "2026-10-07")] == [200.0]


def test_봉_정합성이_깨진_행은_빼고_부분_실패로_드러낸다(tmp_path):
    # WHY: 고가 < 저가 같은 봉은 지수에서도 물리적으로 불가능하다 — 조용히 canonical 에 앉으면
    # 하류가 사실로 읽는다. 행은 빼고 exit 2 로 드러낸다(SFN 은 2 를 부분 성공으로 이어 간다).
    storage = LocalStorage(tmp_path)
    _write_raw(storage, "R1", [_row("1005", "20261008"),
                               _row("1006", "20261008", high="10.00", low="20.00")])

    assert normalize_sector_index_daily.run(storage, "N1", "R1") == 2

    assert [r["code"] for r in _canonical(storage, "2026-10-08")] == ["1005"]


def test_NAV_raw_는_읽지_않는다(tmp_path):
    # WHY: 두 정제가 한 장치를 공유한다 — 사양의 raw 판별이 새면 NAV 행이 업종 표에 앉는다.
    storage = LocalStorage(tmp_path)
    nav_key = "raw/source=kis/dataset=etf_nav/market=KR/ingest_date=2026-10-08/run_id=R1/part-00000.ndjson"
    storage.put_bytes(nav_key, b'{"stck_bsop_date": "20261008", "nav": "1.0"}\n')

    assert normalize_sector_index_daily.run(storage, "N1", "R1") == 0
    assert storage.list_keys("canonical/market_data/sector_index_daily/") == []
