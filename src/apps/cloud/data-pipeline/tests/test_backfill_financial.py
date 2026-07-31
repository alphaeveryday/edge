"""재무 백필 — **격리·재개·검증·무변형이 계약이다.**

이 네 가지가 백필을 일회성 적재와 가른다.

    격리   포워드(source=dart)와 파티션이 절대 겹치지 않고, draft 접두사가 원장까지 덮는다
    재개   중단된 지점에서 이어지고, 이미 받은 것을 다시 받지 않는다
    검증   쌓였다는 말을 믿지 않고 실제 객체의 sha256 을 매니페스트와 대조한다
    무변형 벤더 열을 고르지 않는다 - 주요계정으로 줄이면 매출·매출원가가 사라진다

망은 타지 않는다. `HfDataset` 을 가짜로 주입한다 - 백필의 계약은 네트워크가 아니라
쓰기 좌표와 원장에 있다.
"""

from __future__ import annotations

import io
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data_pipeline.backfill.financial import DATASET, SOURCE, backfill_financial
from data_pipeline.backfill.hf import HfError, HfFile
from data_pipeline.backfill.manifest import Manifest, manifest_key
from data_pipeline.lake import LocalStorage

ROWS = {
    "005930": [
        {"rcept_no": "20260311001085", "sj_div": "IS", "account_nm": "매출액",
         "thstrm_amount": "1338734", "fs_div": "CFS", "bsns_year": "2026"},
        {"rcept_no": "20260311001085", "sj_div": "IS", "account_nm": "매출원가",
         "thstrm_amount": "800000", "fs_div": "CFS", "bsns_year": "2026"},
    ],
    "000660": [
        {"rcept_no": "20260311002222", "sj_div": "BS", "account_nm": "자산총계",
         "thstrm_amount": "999", "fs_div": "OFS", "bsns_year": "2026"},
    ],
}


class FakeHf:
    """가짜 데이터셋. **oid 를 바꿀 수 있어야** 업스트림 변경 재수집을 검사할 수 있다."""

    repo = "fake/repo"
    revision = "main"

    def __init__(self) -> None:
        self.oids = {t: f"oid-{t}-v1" for t in ROWS}
        self.fetched: list[str] = []
        self.explode: set[str] = set()

    def files(self, folder):
        return [HfFile(path=f"{folder}/{t}.parquet", size=1, oid=o)
                for t, o in self.oids.items()]

    def tickers(self, folder):
        return sorted(self.oids)

    def fetch(self, path):
        ticker = path.rsplit("/", 1)[-1].removesuffix(".parquet")
        self.fetched.append(ticker)
        if ticker in self.explode:
            raise HfError("업스트림 500")
        buf = io.BytesIO()
        pq.write_table(pa.Table.from_pylist(ROWS[ticker]), buf)
        return buf.getvalue()


def _rows(storage: LocalStorage, key: str) -> list[dict]:
    return [json.loads(x) for x in
            storage.get_bytes(key).decode("utf-8").splitlines() if x]


def test_backfill_writes_under_its_own_source_and_run_id(tmp_path):
    """포워드는 source=dart · run_id 접두사가 다르다. **롤백은 이 파티션 삭제다.**"""
    st = LocalStorage(tmp_path)

    log = backfill_financial(st, dataset=FakeHf(), ingest_date="2026-07-31")

    assert log["prefix"] == (
        "raw/source=dartlab/dataset=financial_statements/market=KR"
        "/ingest_date=2026-07-31/run_id=backfill-dartlab-financial-20260731")
    assert all("source=dart/" not in k for k in st.list_keys("raw/"))
    assert log["failed"] == 0 and log["rows"] == 3


def test_draft_prefix_covers_the_ledger_too(tmp_path):
    """초안 매니페스트가 프로덕션 경로에 쓰이면 같은 run_id 로 서로를 덮는다."""
    st = LocalStorage(tmp_path)

    log = backfill_financial(st, dataset=FakeHf(), ingest_date="2026-07-31",
                             key_prefix="draft")

    assert log["prefix"].startswith("draft/raw/")
    assert log["manifest"].startswith("draft/operations_archive/")
    assert st.list_keys("raw/") == [] and st.list_keys("operations_archive/") == []


def test_a_second_run_resumes_instead_of_refetching(tmp_path):
    """이미 받은 것을 다시 받으면 전 종목 백필이 며칠이 된다."""
    st, hf = LocalStorage(tmp_path), FakeHf()
    backfill_financial(st, dataset=hf, ingest_date="2026-07-31")
    first = list(hf.fetched)

    log = backfill_financial(st, dataset=hf, ingest_date="2026-07-31")

    assert hf.fetched == first, "재개인데 다시 받았다"
    assert log["skipped"] == 2 and log["fetched"] == 0


def test_an_upstream_change_is_refetched_because_the_oid_moved(tmp_path):
    """`main` 은 움직이는 참조다. 내용이 바뀌면 다시 받아야 한다 - 시각만으론 못 안다."""
    st, hf = LocalStorage(tmp_path), FakeHf()
    backfill_financial(st, dataset=hf, ingest_date="2026-07-31")
    hf.oids["005930"] = "oid-005930-v2"

    log = backfill_financial(st, dataset=hf, ingest_date="2026-07-31")

    assert log["fetched"] == 1 and log["skipped"] == 1
    assert hf.fetched[-1] == "005930"


def test_a_failure_is_recorded_not_skipped_silently(tmp_path):
    """빠진 것과 실패한 것은 다르다. 세지 않으면 결손이 숨는다."""
    st, hf = LocalStorage(tmp_path), FakeHf()
    hf.explode = {"000660"}

    log = backfill_financial(st, dataset=hf, ingest_date="2026-07-31")

    assert log["failed"] == 1 and log["fetched"] == 1
    man = Manifest.from_bytes(st.get_bytes(log["manifest"]))
    assert man.failed == ["000660"] and "HfError" in man.items["000660"]["error"]


def test_verify_catches_a_tampered_object(tmp_path):
    """"쌓였다"는 말을 믿지 않는다 - 실제 객체를 매니페스트와 대조한다."""
    st = LocalStorage(tmp_path)
    log = backfill_financial(st, dataset=FakeHf(), ingest_date="2026-07-31")
    man = Manifest.from_bytes(st.get_bytes(log["manifest"]))

    assert man.verify(st)["mismatched"] == 0

    st.put_bytes(man.items["005930"]["key"],
                 '{"account_nm":"조작"}\n'.encode("utf-8"))
    got = man.verify(st)
    assert got["mismatched"] == 1 and got["bad"][0]["why"] == "sha256 불일치"


def test_vendor_columns_are_preserved_and_provenance_is_added(tmp_path):
    """열을 고르면 매출·매출원가가 사라진다 - 주요계정 API 의 한계가 여기서 반복된다."""
    st = LocalStorage(tmp_path)
    log = backfill_financial(st, dataset=FakeHf(), ingest_date="2026-07-31")
    man = Manifest.from_bytes(st.get_bytes(log["manifest"]))

    rows = _rows(st, man.items["005930"]["key"])

    assert {r["account_nm"] for r in rows} == {"매출액", "매출원가"}
    for r in rows:                       # 벤더 원본 열이 그대로 있다
        assert r["sj_div"] and r["fs_div"] and r["rcept_no"]
    for key in ("our_ticker", "market", "fetched_at", "backfill_source",
                "backfill_oid"):         # provenance 만 덧붙인다
        assert rows[0][key]
    assert rows[0]["backfill_source"] == "hf:fake/repo@main"


def test_a_manifest_from_another_version_fails_loud():
    """조용히 읽으면 옛 형식을 새 규칙으로 해석해 재개가 어긋난다."""
    with pytest.raises(ValueError, match="version"):
        Manifest.from_bytes(json.dumps({"version": 0, "source": "x"}).encode())


def test_an_unreadable_manifest_stops_instead_of_starting_over(tmp_path):
    """깨진 매니페스트를 새 것으로 덮으면 **유일한 재개·검증 원장이 사라진다.**

    WHY: 다음 save 가 그 자리를 덮어쓰고 이미 끝난 run 을 처음부터 다시 돌린다 - 손상은
    조용히 사라지고, 재처리와 중복 적재의 원인을 사후에 찾을 수 없다. 진짜 '없음'만
    새로 시작해야 한다.
    """
    st = LocalStorage(tmp_path)
    st.put_bytes(manifest_key(SOURCE, DATASET, "backfill-x"), b"{not json")

    with pytest.raises(Exception) as got:      # noqa: PT011 - json 예외 타입은 계약이 아니다
        Manifest.load_or_new(st, source=SOURCE, dataset=DATASET, run_id="backfill-x")
    assert not isinstance(got.value, FileNotFoundError)


def test_a_missing_manifest_still_starts_a_fresh_one(tmp_path):
    """반대 방향 - '없음'까지 막으면 첫 run 이 아예 못 돈다."""
    got = Manifest.load_or_new(LocalStorage(tmp_path), source=SOURCE, dataset=DATASET,
                               run_id="backfill-new", market="KR",
                               ingest_date="2026-07-31", repo="fake/repo",
                               revision="main", folder="financial")

    assert got.items == {} and got.run_id == "backfill-new"


def test_the_manifest_key_is_partitioned_by_source_dataset_and_run():
    got = manifest_key(SOURCE, DATASET, "backfill-x", "draft")
    assert got == ("draft/operations_archive/backfill_manifests/source=dartlab"
                   "/dataset=financial_statements/run_id=backfill-x/manifest.json")
