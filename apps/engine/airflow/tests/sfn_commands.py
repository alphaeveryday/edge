"""SFN 잡 목록(data-pipeline 모듈 tf)의 명령 — 레인 DAG 가 복제한 명령을 인자까지 대조하는 원본(ALPHA-1407·1408).

명령의 정본은 terraform 잡 목록이다(SFN 이 롤백 경로로 남아 있는 동안 두 곳에 있다). DAG 는 Airflow 환경에서
terraform 을 읽지 않으므로 복제하고, 이 대조가 드리프트를 잡는다 — test-airflow 는 data-pipeline 모듈이 바뀌어도
돈다. 인자에 terraform 값(`${var.X}`·`${local.X}`)이 든 잡은 DAG 가 그 자리를 `{{ edge_lane_arg('X') }}` 로 둬야 하고,
값은 data-pipeline 출력 `airflow_lane_args` 의 키 X 가 **같은 var·local** 에서 Airflow env 로 넘긴다(edge_batch.lane_arg).
"""

from __future__ import annotations

import re
from pathlib import Path

TF = Path(__file__).resolve().parents[4] / "infra/terraform/modules/data-pipeline"
_TF_VALUE = re.compile(r"^\$\{(var|local)\.(\w+)\}$")


def _lane_args_output() -> str:
    outputs = (TF / "outputs.tf").read_text()
    block = outputs[outputs.index('output "airflow_lane_args"'):]
    return block[:block.index("\n}\n")]


def sfn_command(state: str) -> tuple[str, list[str]]:
    """(taskdef_key, 명령 토큰) — `$.run_id` 는 그대로, terraform 값은 DAG 가 쓸 lane_arg 템플릿으로 바꾼다.

    잡 목록 항목(`state = …` · `taskdef_key` · `command_expr`)과, 페이즈 빌더 밖에 직접 쓴 직렬 state(뉴스
    `NewsLoadAssertions = merge(…)` 의 `TaskDefinition`·`"Command.$"`)를 둘 다 읽는다."""
    tf = "\n".join(p.read_text() for p in sorted(TF.glob("*.tf")))
    matches = re.findall(rf'state\s+= "{state}"\s+taskdef_key\s+= "(\w+)"\s+command_expr = "States\.Array\((.*)\)"', tf)
    matches += re.findall(rf'^\s*{state} = merge\(local\.ecs_run_task_base, \{{.*?TaskDefinition = '
                          rf'aws_ecs_task_definition\.this\["(\w+)"\]\.arn.*?"Command\.\$" = "States\.Array\((.*?)\)"$',
                          tf, re.M | re.S)
    assert len(matches) == 1, f"{state} 잡이 data-pipeline 모듈에 하나가 아니다: {len(matches)}"
    taskdef, raw = matches[0]
    args = []
    for token in (a.strip().strip("'") for a in raw.split(",")):
        value = _TF_VALUE.match(token)
        if value is None:
            assert "${" not in token, f"{state}: 읽지 못하는 terraform 표현 {token}"
            args.append(token)
            continue
        kind, name = value.groups()
        # SFN 과 DAG 가 같은 값을 받으려면 출력이 그 var·local 을 그대로 넘겨야 한다(상수·다른 변수 금지).
        assert re.search(rf"^\s*{name}\s*=\s*(tostring\()?{kind}\.{name}\)?\s*$", _lane_args_output(), re.M), \
            f"{state}: airflow_lane_args 가 {kind}.{name} 을 넘기지 않는다"
        args.append(f"{{{{ edge_lane_arg('{name}') }}}}")
    return taskdef, args


def dag_command(op) -> list[str]:
    """DAG 스텝의 명령 — run_id 템플릿을 SFN 의 `$.run_id` 로 바꿔 비교할 수 있게 한다."""
    return [("$.run_id" if "edge_run_id" in c else c) for c in op.overrides["containerOverrides"][0]["command"]]
