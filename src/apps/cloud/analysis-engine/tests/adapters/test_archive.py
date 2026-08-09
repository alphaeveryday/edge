"""런 아카이브와 분해 요약 테스트.

모든 런(트리거 없는 잔잔한 종료 포함)은 아카이브 1건을 남겨 중간 산출물이 감사
가능해야 하고, 아카이브 쓰기 실패가 런을 죽여선 안 된다(본업은 영속, 아카이브는 관측).
"""

import json
import pytest
from datetime import date
from types import SimpleNamespace

from edge_analysis.adapters.archive import (MAX_RUN_ARCHIVE_BYTES, RunArchiveError,
                                             decomp_summary, write_run_archive)
from edge_analysis.domain.models import Decomposition, Member

_SETTINGS = SimpleNamespace(
    trade_date=date(2026, 7, 16),
    request_id="req-1",
    etf_ticker="091160",
    lake_bucket="test-lake",
    result_s3_prefix="s3://test-lake/operations_archive/etf_explanations/",
)


class _FakeS3:
    def __init__(self, *, fail=False):
        self.puts = []
        self._fail = fail

    def put_object(self, **kwargs):
        if self._fail:
            raise RuntimeError("S3 down")
        self.puts.append(kwargs)


def test_archive_lands_under_the_result_prefix_runs_path():
    # 키는 결과 prefix 하위 runs/ 여야 기존 PutObject IAM 스코프 안이다(밖이면 매 런 AccessDenied).
    s3 = _FakeS3()

    location = write_run_archive(s3, _SETTINGS, {
        "outcome": "explained",
        "explanation": {"verdict": "시장·섹터 주도", "key_evidence": ["e1"], "unexplained": "u"},
    })

    [put] = s3.puts
    assert put["Key"] == ("operations_archive/etf_explanations/runs/etf=091160/"
                          "trade_date=2026-07-16/req-1.json")
    assert location == f"s3://test-lake/{put['Key']}"
    body = json.loads(put["Body"].decode("utf-8"))
    assert body["explanation"]["key_evidence"] == ["e1"]  # LLM 원문 보존
    assert body["explanation"]["unexplained"] == "u"
    assert body["trade_date"] == "2026-07-16"


def test_archive_write_failure_is_required_and_propagates():
    with pytest.raises(RunArchiveError, match="S3 down"):
        write_run_archive(_FakeS3(fail=True), _SETTINGS, {"outcome": "explained"})


def test_analysis_run_archive_accepts_boundary_and_rejects_one_byte_over():
    base_s3 = _FakeS3()
    write_run_archive(base_s3, _SETTINGS, {"padding": ""})
    base_size = len(base_s3.puts[0]["Body"])
    padding = "x" * (MAX_RUN_ARCHIVE_BYTES - base_size)

    boundary_s3 = _FakeS3()
    write_run_archive(boundary_s3, _SETTINGS, {"padding": padding})
    assert len(boundary_s3.puts[0]["Body"]) == MAX_RUN_ARCHIVE_BYTES

    with pytest.raises(RunArchiveError, match="RUN_ARCHIVE_TOO_LARGE"):
        write_run_archive(_FakeS3(), _SETTINGS, {"padding": padding + "x"})


def test_decomp_summary_caps_members_at_ten():
    members = [Member(f"T{i}", f"T{i}", 0.01, 0.01, 0.01, i) for i in range(1, 31)]
    decomp = Decomposition(members=members, proxy_ret=0.05, covered_weight=0.9,
                           total_weight=1.0, coverage=0.9, top1=0.4, top3=0.7,
                           advancing=20, total_priced=30, n_constituents=36)

    summary = decomp_summary(decomp)

    assert len(summary["top_members"]) == 10
    assert summary["proxy_ret"] == 0.05
    assert summary["n_constituents"] == 36
