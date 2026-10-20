"""SFN 잡 목록(statemachine.tf)의 명령 — 레인 DAG 가 복제한 명령을 인자까지 대조하는 원본(ALPHA-1407).

명령의 정본은 terraform 잡 목록이다(SFN 이 롤백 경로로 남아 있는 동안 두 곳에 있다). DAG 는 Airflow 환경에서
terraform 을 읽지 않으므로 복제하고, 이 대조가 드리프트를 잡는다 — test-airflow 는 data-pipeline 모듈이 바뀌어도
돈다. 인자에 terraform 변수(`${var.…}`)가 섞인 잡은 아직 대조 규칙이 없어 여기서 실패한다: 그 레인을 옮길 때
값을 Airflow 에 어떻게 넘길지(같은 변수에서 주입) 정하고 이 대조를 함께 넓힌다.
"""

from __future__ import annotations

import re
from pathlib import Path

TF = Path(__file__).resolve().parents[4] / "infra/terraform/modules/data-pipeline"


def sfn_command(state: str) -> tuple[str, list[str]]:
    """(taskdef_key, 명령 토큰) — `$.run_id` 는 그대로 둔다."""
    tf = (TF / "statemachine.tf").read_text()
    match = re.search(rf'state\s+= "{state}"\s+taskdef_key\s+= "(\w+)"\s+command_expr = "States\.Array\((.*)\)"',
                      tf)
    assert match, f"{state} 잡이 statemachine.tf 에 없다"
    args = [a.strip().strip("'") for a in match.group(2).split(",")]
    assert not any("${" in a for a in args), f"{state}: terraform 변수 인자는 아직 대조 규칙이 없다 {args}"
    return match.group(1), args


def dag_command(op) -> list[str]:
    """DAG 스텝의 명령 — run_id 템플릿을 SFN 의 `$.run_id` 로 바꿔 비교할 수 있게 한다."""
    return [("$.run_id" if "edge_run_id" in c else c) for c in op.overrides["containerOverrides"][0]["command"]]
