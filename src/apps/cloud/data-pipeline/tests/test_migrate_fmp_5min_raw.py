"""migrate_fmp_5min_raw — 옮긴 것이 원본과 같고, 못 옮긴 것이 성공으로 안 보이는가 (ALPHA-1104).

이관 도구가 지켜야 할 것은 셋이다: 재실행이 같은 자리를 본다(멱등·재개), raw 의 수신일을
복사한 날로 바꾸지 않는다, 부분 실패·불일치를 success 로 접지 않는다. 각 테스트는 그중
하나가 깨지면 실패한다(Rule 9).
"""

import hashlib
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "migrate_fmp_5min_raw",
    Path(__file__).resolve().parents[1] / "scripts" / "migrate_fmp_5min_raw.py",
)
mig = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mig)

_US = "raw/fmp_5min_us/"


def _obj(name: str, etag: str = "abc", day: int = 25, prefix: str = _US) -> dict:
    return {"Key": prefix + name, "ETag": f'"{etag}"', "Size": 10,
            "LastModified": datetime(2026, 7, day, 15, 0, tzinfo=timezone.utc)}


class _Err(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class _FakeS3:
    """head·copy·get 만 흉내낸다. 키 → (etag, body)."""

    class exceptions:  # noqa: N801 - boto3 모양
        ClientError = _Err

    def __init__(self, objects: dict[str, tuple[str, bytes]]):
        self.objects = dict(objects)
        self.copies: list[str] = []

    def head_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise _Err("404")
        return {"ETag": f'"{self.objects[Key][0]}"'}

    def copy_object(self, Bucket, Key, CopySource):  # noqa: N803
        body = self.objects[CopySource["Key"]][1]
        self.objects[Key] = (hashlib.md5(body).hexdigest(), body)  # noqa: S324
        self.copies.append(Key)

    def get_object(self, Bucket, Key):  # noqa: N803
        body = self.objects[Key][1]

        class _B:
            def iter_chunks(self, n):
                yield body
        return {"Body": _B()}


def test_destination_is_a_function_of_the_source_not_of_today():
    """목적 키는 원본 LastModified 날짜와 프리픽스 해시로만 정해진다.

    WHY: 기존 KR 복사는 ingest_date 에 **복사한 날**(07-29)을, run_id 에 난수를 넣었다.
    그러면 재실행마다 새 목적지가 생겨 멱등이 깨지고, raw 의 수신일이 복사 시각으로 바뀐다.
    """
    todo, _ = mig.plan([_obj("A.parquet", day=25), _obj("B.parquet", day=26)], "us",
                       mig.default_run_id(_US), None, "", 0)
    again, _ = mig.plan([_obj("A.parquet", day=25), _obj("B.parquet", day=26)], "us",
                        mig.default_run_id(_US), None, "", 0)
    assert [t["dest_key"] for t in todo] == [t["dest_key"] for t in again]
    assert "/ingest_date=2026-07-25/" in todo[0]["dest_key"]
    assert "/ingest_date=2026-07-26/" in todo[1]["dest_key"]
    assert todo[0]["dest_key"].startswith("raw/source=fmp/dataset=price_5min/market=US/")


def test_every_source_object_is_accounted_for():
    """데이터가 아닌 객체도 제외 사유와 함께 남는다 — 빠진 것과 못 본 것을 가른다."""
    objs = [_obj("A.parquet"), _obj("A.cursor.json"), _obj("harvest.log"),
            _obj("sub/x.parquet")]
    todo, excluded = mig.plan(objs, "us", "run_x", None, "", 0)
    assert [t["src_key"] for t in todo] == [_US + "A.parquet"]
    assert len(todo) + len(excluded) == len(objs)
    assert all(e["status"] == "excluded" for e in excluded)


def test_kr_preset_keeps_only_ticker_files():
    """KR 본체 프리픽스의 kospi200_proxy.parquet 은 가격 raw 가 아니다(duck 글롭 사고와 같은 자리)."""
    kr = "raw/kr_intraday/fmp_5min/"
    todo, excluded = mig.plan([_obj("005930.KS.parquet", prefix=kr),
                               _obj("kospi200_proxy.parquet", prefix=kr)],
                              "kr", "run_x", None, "", 0)
    assert [t["src_key"] for t in todo] == [kr + "005930.KS.parquet"]
    assert excluded[0]["src_key"] == kr + "kospi200_proxy.parquet"


def test_rerun_after_copy_is_a_no_op():
    """두 번째 실행은 복사하지 않고 같은 바이트임을 확인만 한다(재개 = 그냥 다시 돌리기)."""
    body = b"parquet-bytes"
    s3 = _FakeS3({_US + "A.parquet": (hashlib.md5(body).hexdigest(), body)})  # noqa: S324
    todo, _ = mig.plan([_obj("A.parquet", etag=hashlib.md5(body).hexdigest())],  # noqa: S324
                       "us", "run_x", None, "", 0)
    first = mig.migrate_one(s3, "b", todo[0], dry_run=False)
    second = mig.migrate_one(s3, "b", todo[0], dry_run=False)
    assert (first["status"], second["status"]) == ("copied", "already_identical")
    assert len(s3.copies) == 1


def test_multipart_source_is_verified_by_content_not_etag():
    """멀티파트 원본의 ETag(`…-N`)는 MD5 가 아니다 — 내용으로 대조해야 같은 바이트를 같다고 본다."""
    body = b"big-file"
    s3 = _FakeS3({_US + "A.parquet": ("deadbeef-3", body)})
    todo, _ = mig.plan([_obj("A.parquet", etag="deadbeef-3")], "us", "run_x", None, "", 0)
    assert mig.migrate_one(s3, "b", todo[0], dry_run=False)["status"] == "copied"


def test_different_bytes_at_destination_are_not_overwritten_and_fail_the_run():
    """목적지에 다른 바이트가 있으면 덮지 않고 mismatch — 전체 status 는 success 가 아니다."""
    s3 = _FakeS3({_US + "A.parquet": ("aaa", b"new"), })
    todo, _ = mig.plan([_obj("A.parquet", etag="aaa")], "us", "run_x", None, "", 0)
    s3.objects[todo[0]["dest_key"]] = ("zzz", b"old")
    r = mig.migrate_one(s3, "b", todo[0], dry_run=False)
    assert r["status"] == "mismatch" and s3.objects[todo[0]["dest_key"]][1] == b"old"
    assert mig.summarize([r, {"status": "copied"}]) == "partial"
    assert mig.summarize([{"status": "copied"}, {"status": "already_identical"}]) == "success"


def test_dry_run_writes_nothing():
    s3 = _FakeS3({_US + "A.parquet": ("aaa", b"x")})
    todo, _ = mig.plan([_obj("A.parquet", etag="aaa")], "us", "run_x", None, "", 0)
    assert mig.migrate_one(s3, "b", todo[0], dry_run=True)["status"] == "would_copy"
    assert s3.copies == []
