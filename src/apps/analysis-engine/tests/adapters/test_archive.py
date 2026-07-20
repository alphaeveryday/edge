"""Tests for the run archive and its decomposition summary.

Every run (including a calm no-trigger exit) must leave one archived record so
intermediate outputs stay auditable, and an archive write must never take the
run down: persistence is the job, observation is not.
"""

import json
from datetime import date
from types import SimpleNamespace

from edge_analysis.adapters.archive import decomp_summary, write_run_archive
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
    # The key must sit under the result prefix's runs/ so the existing PutObject
    # IAM scope covers it; anywhere else is an AccessDenied every run.
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
    assert body["explanation"]["key_evidence"] == ["e1"]  # raw LLM fields survive
    assert body["explanation"]["unexplained"] == "u"
    assert body["trade_date"] == "2026-07-16"


def test_archive_write_failure_returns_none_instead_of_raising():
    assert write_run_archive(_FakeS3(fail=True), _SETTINGS, {"outcome": "explained"}) is None


def test_decomp_summary_caps_members_at_ten():
    members = [Member(f"T{i}", f"T{i}", 0.01, 0.01, 0.01, i) for i in range(1, 31)]
    decomp = Decomposition(members=members, proxy_ret=0.05, covered_weight=0.9,
                           total_weight=1.0, coverage=0.9, top1=0.4, top3=0.7,
                           advancing=20, total_priced=30, n_constituents=36)

    summary = decomp_summary(decomp)

    assert len(summary["top_members"]) == 10
    assert summary["proxy_ret"] == 0.05
    assert summary["n_constituents"] == 36
