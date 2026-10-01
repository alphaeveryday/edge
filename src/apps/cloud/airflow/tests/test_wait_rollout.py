"""deploy-airflow 서비스 교체 완료 판정(deploy/wait_rollout.sh, ALPHA-1138).

2026-10-01 두 배포가 services-stable 직후 PRIMARY `IN_PROGRESS` 를 한 번 보고 실패로 끝났다. 실제로는 21·26초 뒤
COMPLETED 였다. 정상 지연을 기다리되, 롤백·FAILED 는 즉시, 끝나지 않는 rollout 은 제한 시간에 실패해야 한다 —
배포 실패를 성공으로 보고하면 옛 리비전이 도는 채로 다음 단계가 진행된다. 판정 대상은 이번 배포의 id 라서,
같은 리비전이라도 다른 배포의 COMPLETED 를 이번 성공으로 세지 않는다.
가짜 `aws` 가 describe-services 응답을 차례로 돌려준다(마지막 응답은 반복). 실제 AWS 는 부르지 않는다.
"""
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "wait_rollout.sh"
NEW = "arn:aws:ecs:ap-northeast-2:393229433969:task-definition/edge-dev-airflow:14"
OLD = "arn:aws:ecs:ap-northeast-2:393229433969:task-definition/edge-dev-airflow:13"
DEP = "ecs-svc/9109411264556166088"  # 10-01 본 실험 배포 id(서비스 이벤트)
OTHER = "ecs-svc/4655079256076546834"


def run(tmp_path, states, timeout=60):
    (tmp_path / "states").write_text("\n".join(states) + "\n")
    fake = tmp_path / "aws"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'n=$(( $(cat "$T/n" 2>/dev/null || echo 0) + 1 )); echo $n > "$T/n"\n'
        'line=$(sed -n "${n}p" "$T/states"); [ -n "$line" ] || line=$(tail -n1 "$T/states")\n'
        'printf "%b\\n" "$line"\n'
    )
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "T": str(tmp_path),
           "ROLLOUT_TIMEOUT": str(timeout), "ROLLOUT_POLL": "0"}
    p = subprocess.run(["bash", str(SCRIPT), "edge-dev-airflow", "edge-dev-airflow", DEP],
                       env=env, capture_output=True, text=True, timeout=30)
    calls = int((tmp_path / "n").read_text())
    return p.returncode, p.stdout, calls


def test_in_progress_then_completed_succeeds(tmp_path):
    # 10-01 실측 모양: stable 직후 IN_PROGRESS → 잠시 뒤 COMPLETED. 옛 판정은 첫 줄에서 실패했다.
    code, out, calls = run(tmp_path, [rf"{DEP}\t{NEW}\tIN_PROGRESS", rf"{DEP}\t{NEW}\tIN_PROGRESS",
                                      rf"{DEP}\t{NEW}\tCOMPLETED"])
    assert (code, calls) == (0, 3), out


def test_failed_rollout_fails_without_waiting(tmp_path):
    code, out, calls = run(tmp_path, [rf"{DEP}\t{NEW}\tFAILED", rf"{DEP}\t{NEW}\tCOMPLETED"])
    assert (code, calls) == (1, 1), out


def test_rolled_back_primary_fails_even_if_completed(tmp_path):
    # circuit breaker 롤백: 새 배포가 옛 리비전으로 COMPLETED — 서비스는 멀쩡하지만 이번 배포는 실패다.
    code, out, calls = run(tmp_path, [rf"{OTHER}\t{OLD}\tCOMPLETED"])
    assert (code, calls) == (1, 1), out


def test_other_deployment_of_same_revision_is_not_this_success(tmp_path):
    # 같은 리비전이라도 PRIMARY 가 이번 배포가 아니면(다른 배포가 덮음) 그 COMPLETED 는 이번 결과가 아니다.
    code, out, calls = run(tmp_path, [rf"{OTHER}\t{NEW}\tCOMPLETED"])
    assert (code, calls) == (1, 1), out


def test_rollout_that_never_completes_times_out(tmp_path):
    code, out, calls = run(tmp_path, [rf"{DEP}\t{NEW}\tIN_PROGRESS"], timeout=0)
    assert code == 1 and "초 안에 끝나지 않았다" in out and calls == 1, out
