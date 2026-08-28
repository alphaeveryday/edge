"""Lake artifact 수명 계약."""

from pathlib import Path


_STORAGE_TF = (Path(__file__).resolve().parents[5]
               / "infra/terraform/modules/pipeline/storage.tf")


def test_run_scoped_canonical_snapshots_have_bounded_retention():
    """WHY(ALPHA-1045): 분당 normalizer가 전체 날짜 파티션 snapshot을 run별로 남기므로
    lifecycle이 빠지면 저장량이 파티션 크기 × run 수로 영구 증가한다."""
    terraform = _STORAGE_TF.read_text(encoding="utf-8")

    lifecycle = terraform.split(
        'resource "aws_s3_bucket_lifecycle_configuration" "canonical_run_artifacts"', 1
    )[1].split("# lake 전환 후", 1)[0]
    assert 'prefix = "operations_archive/canonical_run_artifacts/"' in lifecycle
    assert "days = 30" in lifecycle

