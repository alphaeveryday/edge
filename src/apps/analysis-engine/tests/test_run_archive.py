"""런 아카이브 테스트 (ALPHA-415).

검사하는 WHY: 아카이브가 안 남으면 정상 런의 중간 산출물(분해·트리거·LLM 원문)이
stdout 로그뿐이라 사후 감사가 불가능하고, 아카이브 기록 실패가 런을 죽이면
관측이 본업(분석 영속)을 무너뜨린다.
"""

import json
from datetime import date
from types import SimpleNamespace

from edge_analysis.daily_pipeline import _decomp_summary, write_run_archive

_SETTINGS = SimpleNamespace(
    trade_date=date(2026, 7, 16),
    request_id="req-1",
    etf_ticker="091160",
    lake_bucket="test-lake",
    result_s3_prefix="s3://test-lake/operations_archive/etf_explanations/",
)


class _FakeS3:
    def __init__(self, fail=False):
        self.puts = []
        self._fail = fail

    def put_object(self, **kwargs):
        if self._fail:
            raise RuntimeError("S3 down")
        self.puts.append(kwargs)


def test_archive_lands_under_result_prefix_runs():
    """키가 결과 prefix 하위 runs/ 여야 기존 PutObject IAM 스코프 안이다 — 밖이면
    AccessDenied 로 매 런이 관측 실패를 찍는다."""
    s3 = _FakeS3()
    location = write_run_archive(s3, _SETTINGS, {
        "outcome": "explained",
        "explanation": {"verdict": "시장·섹터 주도", "key_evidence": ["e1"], "unexplained": "u"},
    })

    [put] = s3.puts
    assert put["Bucket"] == "test-lake"
    assert put["Key"] == ("operations_archive/etf_explanations/runs/etf=091160/"
                          "trade_date=2026-07-16/req-1.json")
    assert location == f"s3://test-lake/{put['Key']}"
    body = json.loads(put["Body"].decode("utf-8"))
    # LLM 원문(매핑 손실 필드 포함)이 그대로 남아야 아카이브의 존재 이유가 선다.
    assert body["explanation"]["key_evidence"] == ["e1"]
    assert body["explanation"]["unexplained"] == "u"
    assert body["trade_date"] == "2026-07-16"


def test_archive_failure_does_not_raise():
    """아카이브 기록 실패는 런을 죽이지 않는다 — 분석 영속이 본업, 아카이브는 관측."""
    assert write_run_archive(_FakeS3(fail=True), _SETTINGS, {"outcome": "explained"}) is None


def test_decomp_summary_caps_members():
    """전 종목 기여도(수백 행)는 아카이브를 데이터로 만든다 — 상위 10개 + 스칼라 전부."""
    decomp = {
        "proxy_ret": 0.05, "coverage": 0.9, "covered_weight": 0.9, "total_priced": 30,
        "n_constituents": 36, "advancing": 20, "top1": 0.4, "top3": 0.7,
        "members": [{"ticker": f"T{i}", "contribution": 0.01} for i in range(30)],
    }
    summary = _decomp_summary(decomp)
    assert len(summary["top_members"]) == 10
    assert summary["proxy_ret"] == 0.05 and summary["n_constituents"] == 36
