"""실 S3 요청 계약과 로컬 게시 경계 — 경합·권한 장애가 불변 바이트를 바꾸면 안 된다."""

import io
import os

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.stub import Stubber
import pytest

from data_pipeline.lake import LocalStorage, S3Storage
from data_pipeline.minute.artifacts import (
    ArtifactImmutabilityError, ArtifactWriteConflictError, put_immutable, sha256_bytes,
)


@pytest.fixture
def s3():
    storage = S3Storage("test-bucket")
    storage._client = boto3.client(
        "s3", region_name="ap-northeast-2", aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    with Stubber(storage.client) as stub:
        yield storage, stub
        stub.assert_no_pending_responses()


def _put_params(data=b"winner"):
    return {"Bucket": "test-bucket", "Key": "candidate", "Body": data, "IfNoneMatch": "*"}


def _get_params():
    return {"Bucket": "test-bucket", "Key": "candidate"}


def test_s3_initial_put_is_conditional_without_list(s3):
    storage, stub = s3
    stub.add_response("put_object", {}, _put_params())
    assert put_immutable(storage, "candidate", b"winner") == sha256_bytes(b"winner")


@pytest.mark.parametrize(("code", "status"), [("PreconditionFailed", 412), ("ConditionalRequestConflict", 409)])
@pytest.mark.parametrize("same_content", [True, False])
def test_s3_conflict_reads_winner_and_never_overwrites(s3, code, status, same_content):
    storage, stub = s3
    data = b"winner" if same_content else b"loser"
    stub.add_client_error("put_object", code, http_status_code=status, expected_params=_put_params(data))
    stub.add_response("get_object", {"Body": io.BytesIO(b"winner"), "ETag": '"etag"'}, _get_params())
    if same_content:
        assert put_immutable(storage, "candidate", data) == sha256_bytes(data)
    else:
        with pytest.raises(ArtifactImmutabilityError, match="candidate"):
            put_immutable(storage, "candidate", data)


def test_s3_missing_after_conflict_can_retry_conditional_creation(s3):
    storage, stub = s3
    stub.add_client_error("put_object", "ConditionalRequestConflict", http_status_code=409, expected_params=_put_params())
    stub.add_client_error("get_object", "NoSuchKey", http_status_code=404, expected_params=_get_params())
    stub.add_response("put_object", {}, _put_params())
    assert put_immutable(storage, "candidate", b"winner") == sha256_bytes(b"winner")


def test_s3_repeated_missing_conflict_is_bounded_failure(s3):
    storage, stub = s3
    for _ in range(3):
        stub.add_client_error("put_object", "ConditionalRequestConflict", http_status_code=409, expected_params=_put_params())
        stub.add_client_error("get_object", "NoSuchKey", http_status_code=404, expected_params=_get_params())
    with pytest.raises(ArtifactWriteConflictError, match="candidate"):
        put_immutable(storage, "candidate", b"winner")


@pytest.mark.parametrize("operation", ["put_object", "get_object"])
def test_s3_access_denied_is_not_a_miss_or_conflict(s3, operation):
    storage, stub = s3
    if operation == "get_object":
        stub.add_client_error("put_object", "PreconditionFailed", http_status_code=412, expected_params=_put_params())
    stub.add_client_error(operation, "AccessDenied", http_status_code=403,
                          expected_params=_get_params() if operation == "get_object" else _put_params())
    with pytest.raises(ClientError) as error:
        put_immutable(storage, "candidate", b"winner")
    assert error.value.response["Error"]["Code"] == "AccessDenied"


def test_network_failure_is_not_retried_as_missing(s3, monkeypatch):
    storage, _stub = s3
    error = EndpointConnectionError(endpoint_url="https://s3.invalid")

    def unavailable(**kwargs):
        raise error

    monkeypatch.setattr(storage.client, "put_object", unavailable)
    with pytest.raises(EndpointConnectionError) as caught:
        put_immutable(storage, "candidate", b"winner")
    assert caught.value is error


def test_local_publishes_only_complete_bytes_and_hides_staging(tmp_path, monkeypatch):
    storage = LocalStorage(tmp_path)
    payload = b"complete" * 100_000
    link = os.link
    observations = []

    def inspect_publish(source, destination):
        assert not destination.exists()
        assert storage.list_keys("") == []
        with open(source, "rb") as staged:
            assert staged.read() == payload
        link(source, destination)
        observations.append(LocalStorage(tmp_path).get_bytes("candidate"))

    monkeypatch.setattr(os, "link", inspect_publish)
    assert put_immutable(storage, "candidate", payload) == sha256_bytes(payload)
    assert observations == [payload]
    assert [p.name for p in tmp_path.iterdir()] == ["candidate"]


def test_local_failed_publication_cleans_staging_and_propagates(tmp_path, monkeypatch):
    def denied(*args):
        raise PermissionError("publish denied")

    monkeypatch.setattr(os, "link", denied)
    with pytest.raises(PermissionError, match="publish denied"):
        put_immutable(LocalStorage(tmp_path), "candidate", b"data")
    assert list(tmp_path.iterdir()) == []


def test_empty_artifact_is_content_not_missing(tmp_path):
    storage = LocalStorage(tmp_path)
    assert put_immutable(storage, "empty", b"") == sha256_bytes(b"")
    assert put_immutable(LocalStorage(tmp_path), "empty", b"") == sha256_bytes(b"")
    with pytest.raises(ArtifactImmutabilityError):
        put_immutable(storage, "empty", b"nonempty")
