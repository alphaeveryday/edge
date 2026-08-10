"""설명 소비자 오토스케일링 계단 계약 (ALPHA-912).

⚠️ **step 의 bound 는 지표값이 아니라 `지표값 - threshold` 오프셋이다.** 이 한 칸이
어긋나면 apply 는 멀쩡히 통과하고 **대수만 조용히 틀린다** — 특히 깊이 0 이 첫 구간에
걸리면 0 대로 못 내려가 `min_capacity = 0` 의 비용 근거가 사라진다(야간에 계속 뜬다).
terraform 은 그 산술을 검사해 주지 않으므로 여기서 depth→capacity 를 **직접 계산해**
대조한다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REL = "infra/terraform/modules/data-pipeline/analysis_autoscaling.tf"

# 깊이 → 기대 대수. 경계를 **양쪽에서** 집는다(구간 안쪽 하나로는 off-by-one 이 안 잡힌다).
_EXPECTED = [
    (0, 0),                      # 버스트 종료 — 0 대로 내려가야 한다
    (1, 1), (5, 1),              # 첫 구간의 양 끝
    (6, 2), (15, 2),
    (16, 3), (30, 3),
    (31, 4), (200, 4),           # 상단은 열려 있다
]


def _tf() -> str:
    here = Path(__file__).resolve()
    return next((p / _REL).read_text() for p in here.parents if (p / _REL).exists())


def _threshold(text: str) -> float:
    m = re.search(r"^\s*threshold\s*=\s*([0-9.]+)", text, re.M)
    assert m, "알람 threshold 를 못 찾았다 — 이 계약 검사가 헛돌고 있다"
    return float(m.group(1))


def _steps(text: str) -> list[tuple[float, float, int]]:
    """(lower, upper, capacity) 목록. 없는 bound 는 ∓inf."""
    block = re.search(r"step_scaling_policy_configuration\s*\{(.*)\n  \}", text, re.S)
    assert block, "step_scaling_policy_configuration 을 못 찾았다"
    out = []
    for raw in re.findall(r"step_adjustment\s*\{([^}]*)\}", block.group(1)):
        lo = re.search(r"metric_interval_lower_bound\s*=\s*(-?[0-9.]+)", raw)
        up = re.search(r"metric_interval_upper_bound\s*=\s*(-?[0-9.]+)", raw)
        adj = re.search(r"scaling_adjustment\s*=\s*(-?\d+)", raw)
        assert adj, f"scaling_adjustment 없는 step: {raw!r}"
        out.append((float(lo.group(1)) if lo else float("-inf"),
                    float(up.group(1)) if up else float("inf"),
                    int(adj.group(1))))
    assert out, "step_adjustment 가 하나도 없다"
    return out


def _capacity_for(depth: int, threshold: float, steps) -> int:
    """AWS 계약: 오프셋 = 지표값 - threshold.

    ⚠️ 포함/배제는 **한 방향이 아니다** — 지표가 임계 위면 `[lo, up)`, 아래면 `(lo, up]`
    로 뒤집힌다. 오프셋이 bound 에 정확히 얹히는 값이 있으면 그 뒤집힘이 대수를 가르고,
    apply 는 멀쩡히 통과한다. 그래서 여기선 **두 해석을 다 계산해 일치를 요구**한다 —
    일치해야만 계단이 벤더의 규칙 방향과 무관해진다(임계를 반정수로 두는 이유).
    """
    offset = depth - threshold
    above = [adj for lo, up, adj in steps if lo <= offset < up]
    below = [adj for lo, up, adj in steps if lo < offset <= up]
    assert len(above) == 1 and len(below) == 1, \
        f"깊이 {depth}(오프셋 {offset})에 맞는 구간이 {len(above)}/{len(below)}개다 — 구멍이거나 겹친다"
    assert above[0] == below[0], (
        f"깊이 {depth}(오프셋 {offset})이 bound 위에 얹혀 포함/배제 규칙에 따라 "
        f"{above[0]}대와 {below[0]}대로 갈린다 — 임계를 반정수로 두면 사라진다")
    return above[0]


@pytest.mark.parametrize("depth,expected", _EXPECTED)
def test_큐_깊이가_대수를_정한다(depth, expected):
    try:
        text = _tf()
    except StopIteration:
        pytest.skip(f"{_REL} 를 찾을 수 없음 — 저장소 체크아웃에서만 도는 계약 검사")

    assert _capacity_for(depth, _threshold(text), _steps(text)) == expected


def test_계단이_상한을_넘지_않는다():
    """계단 상단이 `max_capacity` 를 넘으면 그 값은 조용히 잘린다 — 상한이 DB 보호선인데
    계단만 보고 올리면 보호선을 넘긴 줄 모른다(변수 기본값이 정본)."""
    try:
        text = _tf()
    except StopIteration:
        pytest.skip(f"{_REL} 를 찾을 수 없음 — 저장소 체크아웃에서만 도는 계약 검사")

    here = Path(__file__).resolve()
    rel = "infra/terraform/modules/data-pipeline/variables.tf"
    variables = next((p / rel).read_text() for p in here.parents if (p / rel).exists())
    block = re.search(r'variable "analysis_consumer_max_capacity"\s*\{(.*?)\n\}', variables, re.S)
    assert block, "analysis_consumer_max_capacity 변수가 없다 — 상한이 배선에서 사라졌다"
    default = re.search(r"default\s*=\s*(\d+)", block.group(1))
    assert default, "상한 기본값이 없다"

    top = max(adj for _, _, adj in _steps(text))
    assert top <= int(default.group(1)), \
        f"계단 상단({top})이 max_capacity({default.group(1)})를 넘는다 — 그 구간은 조용히 잘린다"


def test_처리중_메시지가_잔여_일감에_들어간다():
    """가시 수만 보면 **마지막 한 건이 영영 안 끝난다** — 소비자가 집는 순간 비가시가 돼
    깊이 0 이 되고, OK 로 풀린 알람이 매분 0 대를 써서 처리 중이던 태스크를 죽인다
    (Fargate stopTimeout 120초 < 건당 588초). 재배달되면 같은 순환이라 DLQ 로 간다.
    그래서 `NotVisible` 이 지표에 **반드시 더해져** 있어야 한다."""
    try:
        text = _tf()
    except StopIteration:
        pytest.skip(f"{_REL} 를 찾을 수 없음 — 저장소 체크아웃에서만 도는 계약 검사")

    assert "ApproximateNumberOfMessagesVisible" in text, "가시 메시지 지표가 없다"
    assert "ApproximateNumberOfMessagesNotVisible" in text, \
        "처리 중(비가시) 메시지가 잔여 일감에서 빠졌다 — 인플라이트 중에 0 대로 내려간다"

    expr = re.search(r'expression\s*=\s*"([^"]+)"', text)
    assert expr, "두 지표를 합치는 metric math 식이 없다"
    ids = set(re.findall(r"\b[a-z_]+\b", expr.group(1)))
    for metric in ("ApproximateNumberOfMessagesVisible", "ApproximateNumberOfMessagesNotVisible"):
        hit = re.search(r'metric_name\s*=\s*"' + metric + r'"', text)
        assert hit, f"{metric} 이 metric_query 안에 없다"
        # 그 지표를 감싼 블록의 id = 지표 앞에 마지막으로 선언된 id
        mid = re.findall(r'id\s*=\s*"([a-z_]+)"', text[:hit.start()])[-1]
        assert mid in ids, \
            f"{metric}({mid}) 이 합산식 `{expr.group(1)}` 에 안 쓰인다 — 지표에서 빠졌다"


def test_계단_하한이_0_대에_닿는다():
    """`min_capacity = 0` 은 계단이 실제로 0 을 낼 수 있어야 성립한다. threshold 를 0 으로
    되돌리면 깊이 0 이 첫 구간에 걸려 1 대가 남고, 야간 비용이 조용히 계속 든다."""
    try:
        text = _tf()
    except StopIteration:
        pytest.skip(f"{_REL} 를 찾을 수 없음 — 저장소 체크아웃에서만 도는 계약 검사")

    assert _capacity_for(0, _threshold(text), _steps(text)) == 0, \
        "깊이 0 에서 0 대로 못 내려간다 — threshold 와 계단 원점이 어긋났다"
    assert re.search(r"min_capacity\s*=\s*0", text), "min_capacity 가 0 이 아니다"
