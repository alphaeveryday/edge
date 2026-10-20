"""비공개 저장소와 공개 게시 위치. S3 와 로컬 폴더가 같은 계약을 따른다.

쓰기는 조건부다: 읽을 때 받은 버전(etag)과 지금 버전이 같을 때만 덮어쓴다. 실행이 겹치거나
사람이 같은 파일을 고쳐도 한쪽 변경이 조용히 사라지지 않는다.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Protocol

ABSENT = "absent"  # if_match 에 넣으면 '아직 없을 때만 쓴다'


class ConflictError(RuntimeError):
    """조건부 쓰기의 전제(버전)가 맞지 않았다."""


class Store(Protocol):
    """키-값 저장소."""

    def get(self, key: str) -> tuple[bytes | None, str | None]:
        """(내용, 버전). 없으면 (None, None)."""
        ...

    def put(self, key: str, data: bytes, *, if_match: str | None = None, content_type: str = "application/json; charset=utf-8",
            cache_control: str | None = None) -> str:
        """쓰고 새 버전을 돌려준다. if_match 가 있으면 버전이 맞을 때만 쓴다."""
        ...

    def list(self, prefix: str) -> list[str]:
        """접두어 아래 키 목록."""
        ...

    def delete(self, key: str) -> None:
        """키를 지운다. 없으면 무시한다."""
        ...


class LocalStore:
    """로컬 폴더 저장소 (테스트·로컬 실행). 버전은 내용 해시다."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents and path != self.root.resolve():
            raise ValueError(f"저장소 밖 경로: {key}")
        return path

    def get(self, key):
        path = self._path(key)
        if not path.is_file():
            return None, None
        data = path.read_bytes()
        return data, hashlib.md5(data).hexdigest()

    def put(self, key, data, *, if_match=None, content_type="", cache_control=None):
        path = self._path(key)
        current = hashlib.md5(path.read_bytes()).hexdigest() if path.is_file() else None
        if if_match == ABSENT and current is not None:
            raise ConflictError(f"{key} 가 이미 있습니다.")
        if if_match not in (None, ABSENT) and current != if_match:
            raise ConflictError(f"{key} 가 읽은 뒤 바뀌었습니다.")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        return hashlib.md5(data).hexdigest()

    def list(self, prefix):
        base = self.root
        if not base.exists():
            return []
        return sorted(p.relative_to(base).as_posix() for p in base.rglob("*")
                      if p.is_file() and not p.name.endswith(".tmp") and p.relative_to(base).as_posix().startswith(prefix))

    def delete(self, key):
        self._path(key).unlink(missing_ok=True)


class S3Store:
    """S3 저장소. 조건부 쓰기는 S3 의 If-Match·If-None-Match 를 쓴다."""

    def __init__(self, bucket: str, prefix: str = "", client=None):
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        if client is None:
            import boto3
            client = boto3.client("s3")
        self.client = client

    def get(self, key):
        try:
            res = self.client.get_object(Bucket=self.bucket, Key=self.prefix + key)
        except self.client.exceptions.NoSuchKey:
            return None, None
        return res["Body"].read(), res["ETag"]

    def put(self, key, data, *, if_match=None, content_type="application/json; charset=utf-8", cache_control=None):
        args = {"Bucket": self.bucket, "Key": self.prefix + key, "Body": data, "ContentType": content_type}
        if cache_control:
            args["CacheControl"] = cache_control
        if if_match == ABSENT:
            args["IfNoneMatch"] = "*"
        elif if_match:
            args["IfMatch"] = if_match
        try:
            return self.client.put_object(**args)["ETag"]
        except self.client.exceptions.ClientError as error:
            if error.response.get("Error", {}).get("Code") in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise ConflictError(f"{key}: {error}") from error
            raise

    def list(self, prefix):
        keys = []
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self.prefix + prefix):
            keys += [o["Key"][len(self.prefix):] for o in page.get("Contents", [])]
        return sorted(keys)

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=self.prefix + key)


def open_store(uri: str) -> Store:
    """`s3://버킷/접두어` 또는 로컬 경로."""
    if uri.startswith("s3://"):
        bucket, _, prefix = uri[5:].partition("/")
        return S3Store(bucket, prefix)
    return LocalStore(Path(uri))
