"""OCI compose(infra/oci/vm2·vm3)의 서비스마다 data-pipeline 설정이 기동 단계에서 성립하는지.

의도: VM 에서는 모든 서비스가 같은 .env 를 받고, 서비스별 값은 compose `environment` 로만 얹는다.
공용 .env 에 섹션 일부(예: `MINUTE_PRICE_CONSUMER__DETECTION_POLICY_VERSION`)만 두면 그 섹션이
**모든 컨테이너에** 생기고, 필수 필드(`queue_url`)를 받지 못한 서비스는 `load_settings` 에서 죽는다.
AWS 에선 태스크 정의마다 env 가 따로라 드러나지 않았다(PR #1665 뒤 dry run 에서 session-start 가 exit 1).
서비스마다 (공용 .env.example + 그 서비스의 environment) 로 실제 로더를 돌려 이 종류를 CI 에서 막는다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

from data_pipeline import load_settings

OCI = Path(__file__).resolve().parents[4] / "infra" / "oci"
PIPELINE_IMAGE = "${DATA_PIPELINE_IMAGE:?}"

# .env.example 은 이름만 둔다. 빈 값은 채워서 돌린다 — 지우면 반쪽 섹션도 함께 사라져 결함을 못 본다.
# 타입이 있는 키만 여기 적는다. 새 타입 키가 생기면 검증 실패로 드러나고 여기에 값을 더한다.
SAMPLE = {
    "DATA_PIPELINE_DB__PORT": "5432",
    "DATA_PIPELINE_STORAGE__BACKEND": "s3",
    "DATA_PIPELINE_CALL_BUDGET__ENABLED": "false",
    "DATA_PIPELINE_MINUTE_ARTIFACT_FORMAT": "legacy",
}


def _shared_env(vm: str) -> dict[str, str]:
    env = {}
    for line in (OCI / vm / ".env.example").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            key, _, value = line.partition("=")
            env[key] = value or SAMPLE.get(key, "x")
    return env


def _pipeline_services():
    """data-pipeline 이미지를 쓰는 서비스만 — analysis(VM-2)는 다른 이미지·다른 env 파일이라 이 로더를 안 쓴다."""
    for vm in ("vm2", "vm3"):
        services = yaml.safe_load((OCI / vm / "compose.yaml").read_text())["services"]
        for name, spec in services.items():
            if spec["image"] == PIPELINE_IMAGE:
                assert spec["env_file"] == [".env"], f"{vm}/{name}: 공용 .env 를 받는다는 전제"
                yield pytest.param(vm, spec.get("environment", {}), id=f"{vm}-{name}")


@pytest.mark.parametrize(("vm", "service_env"), list(_pipeline_services()))
def test_every_pipeline_service_loads_settings(vm, service_env, monkeypatch):
    for key in [k for k in os.environ if k.startswith("DATA_PIPELINE_")]:
        monkeypatch.delenv(key)
    assert isinstance(service_env, dict), "environment 는 매핑 형태로 쓴다"
    shared = _shared_env(vm)
    # compose 치환(`${NAME:?}`)은 공용 .env 에서 푼다 — 거기 없는 이름이면 KeyError 로 실패한다
    resolved = {k: re.sub(r"\$\{(\w+)[^}]*\}", lambda m: shared[m.group(1)], str(v))
                for k, v in service_env.items()}
    for key, value in {**shared, **resolved}.items():
        monkeypatch.setenv(key, value)
    load_settings()
