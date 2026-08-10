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
    """`.tf` 본문에서 **주석을 걷어낸** 것.

    ⚠️ 주석을 안 걷으면 단언이 **주석에 걸려 죽는다.** `min_capacity = 0` 가드가 실제로
    그랬다 — 설정을 `1` 로 바꿔도 같은 문자열이 설명 주석 안에 있어 12건이 전부 초록이었고,
    그 가드가 이름 붙인 회귀("야간에 계속 뜬다")를 통째로 통과시켰다. 원문 텍스트를 읽는
    계약 검사는 **코드만** 봐야 한다.
    """
    here = Path(__file__).resolve()
    raw = next((p / _REL).read_text() for p in here.parents if (p / _REL).exists())
    return "\n".join(line.split("#", 1)[0] for line in raw.splitlines())


def _threshold(text: str) -> float:
    m = re.search(r"^\s*threshold\s*=\s*([0-9.]+)", text, re.M)
    assert m, "알람 threshold 를 못 찾았다 — 이 계약 검사가 헛돌고 있다"
    return float(m.group(1))


def _steps(text: str) -> list[tuple[float, float, int]]:
    """(lower, upper, capacity) 목록. 없는 bound 는 ∓inf."""
    # ⚠️ non-greedy — greedy 로 두면 파일의 **마지막** 2칸 `}` 까지 먹어, 이 아래에 중첩
    # 블록을 가진 리소스가 추가되면 그쪽 step_adjustment 가 계약에 조용히 합류한다.
    block = re.search(r"step_scaling_policy_configuration\s*\{(.*?)\n  \}", text, re.S)
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
    ids = {}
    for metric in ("ApproximateNumberOfMessagesVisible", "ApproximateNumberOfMessagesNotVisible"):
        hit = re.search(r'metric_name\s*=\s*"' + metric + r'"', text)
        assert hit, f"{metric} 이 metric_query 안에 없다"
        # 그 지표를 감싼 블록의 id = 지표 앞에 마지막으로 선언된 id
        ids[metric] = re.findall(r'id\s*=\s*"([a-z_]+)"', text[:hit.start()])[-1]

    # ⚠️ id 가 식에 **등장**하는지만 보면 안 된다 — `visible - inflight` 도 통과해 이 검사가
    # 막으려는 결함(가시 단독 판정)으로 되돌아간다. 두 id 를 **더하는** 형태여야 한다.
    a, b = ids["ApproximateNumberOfMessagesVisible"], ids["ApproximateNumberOfMessagesNotVisible"]
    normalized = expr.group(1).replace(" ", "")
    assert normalized in (f"{a}+{b}", f"{b}+{a}"), \
        f"합산식이 `{expr.group(1)}` 이다 — 처리 중 메시지를 **더하는** 식이어야 한다"

    # ⚠️ 알람이 판정하는 것은 `return_data = true` 인 블록 **하나**다. 존재·위치를 따로
    # 세야 한다 — 위치만 보면(있으면 합산식인가) 통째로 지운 변이가 조용히 통과한다.
    judged = [b for b in re.split(r"\bmetric_query\s*\{", text)[1:]
              if re.search(r"return_data\s*=\s*true", b)]
    assert len(judged) == 1, \
        f"return_data = true 인 블록이 {len(judged)}개다 — 알람이 무엇을 판정하는지 정해지지 않는다"
    assert "expression" in judged[0], \
        "return_data 가 합산식이 아닌 개별 지표에 붙어 있다 — 알람이 한쪽만 본다"


def test_계단을_구동하는_배선이_다_있다():
    """계단 **산술**이 맞아도 그것을 구동하는 배선이 빠지면 대수만 조용히 틀린다.

    앞의 검사들은 depth→capacity 표만 본다. 그 표가 성립하려면 넷이 더 필요하고, 넷 다
    빠져도 apply 는 통과한다:
    ① `ExactCapacity` — `ChangeInCapacity` 면 "깊이 31 = 4대"가 "매분 +4"가 돼 모델이 허구가 된다
    ② 알람 집계 = 정책 집계 — 갈리면 계단이 다른 값으로 판정된다
    ③ `alarm_actions` — 없으면 아예 안 올라간다(이 리소스 전체가 무효)
    ④ `ok_actions` — 없으면 버스트 뒤 대수가 안 내려가 `min_capacity = 0` 이 무의미해진다
    그리고 상한은 변수에서 와야 한다 — 하드코딩하면 DB 보호선이 변수 설명에서 떨어져 나간다.
    """
    try:
        text = _tf()
    except StopIteration:
        pytest.skip(f"{_REL} 를 찾을 수 없음 — 저장소 체크아웃에서만 도는 계약 검사")

    assert re.search(r'adjustment_type\s*=\s*"ExactCapacity"', text), \
        "adjustment_type 이 ExactCapacity 가 아니다 — depth→대수 표가 뜻을 잃는다"

    policy_agg = re.search(r'metric_aggregation_type\s*=\s*"(\w+)"', text)
    assert policy_agg, "metric_aggregation_type 이 없다"
    stats = set(re.findall(r'^\s*stat\s*=\s*"(\w+)"', text, re.M))
    assert stats == {policy_agg.group(1)}, \
        f"알람 집계 {stats} 와 정책 집계 {policy_agg.group(1)} 이 갈린다 — 계단이 다른 값으로 판정된다"

    for field, why in (("alarm_actions", "일감이 쌓여도 대수가 안 오른다"),
                       ("ok_actions", "버스트 뒤 대수가 안 내려간다")):
        assert re.search(field + r"\s*=\s*\[aws_appautoscaling_policy\.analysis_scale\.arn\]", text), \
            f"{field} 가 스케일링 정책을 안 부른다 — {why}"

    assert re.search(r"max_capacity\s*=\s*var\.analysis_consumer_max_capacity", text), \
        "max_capacity 가 변수에서 안 온다 — 상한의 DB 근거가 변수 설명에서 떨어져 나간다"

    assert re.search(r'comparison_operator\s*=\s*"GreaterThanThreshold"', text), \
        "비교 방향이 뒤집혔다 — 잔여가 많을 때 OK, 없을 때 ALARM 이 된다"


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
