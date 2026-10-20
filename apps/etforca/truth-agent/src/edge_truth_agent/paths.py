"""사건 JSON 의 칸 경로: `headline[0]`, `body[3].p`, `contrast.after.text`, `behaviors[+]`(끝에 추가).

에이전트는 JSON 문법을 쓰지 않고 이 경로와 값만 넘긴다. 따옴표·이스케이프 실수가 데이터를 깨는 일을 막는다.
"""
from __future__ import annotations

import json
import re

_STEP = re.compile(r"([A-Za-z][A-Za-z0-9_]*)|\[(\d+|\+)\]|\.")
APPEND = "+"


class PathError(ValueError):
    """경로가 틀렸거나 가리키는 칸이 없다."""


def parse(path: str) -> list[str | int]:
    """경로 문자열을 단계 목록으로 바꾼다. `[+]` 는 APPEND 로 남는다."""
    steps: list[str | int] = []
    pos, expect_name = 0, True
    while pos < len(path):
        m = _STEP.match(path, pos)
        if not m or m.end() == pos:
            raise PathError(f"'{path}' 는 칸 경로가 아닙니다. 예: headline[0], body[3].p, contrast.after.text")
        if m.group(1) is not None:
            if not expect_name:
                raise PathError(f"'{path}': 이름 앞에 '.' 이 필요합니다.")
            steps.append(m.group(1))
            expect_name = False
        elif m.group(2) is not None:
            if not steps:
                raise PathError(f"'{path}': 첫 단계는 칸 이름이어야 합니다.")
            steps.append(APPEND if m.group(2) == APPEND else int(m.group(2)))
        else:
            expect_name = True
        pos = m.end()
    if not steps or expect_name:
        raise PathError(f"'{path}' 는 칸 경로가 아닙니다.")
    if APPEND in steps[:-1]:
        raise PathError(f"'{path}': [+] 는 마지막 단계에만 씁니다.")
    return steps


def _child(node, step, path):
    if isinstance(step, int):
        if not isinstance(node, list) or not 0 <= step < len(node):
            raise PathError(f"'{path}': [{step}] 위치가 없습니다.")
        return node[step]
    if not isinstance(node, dict) or step not in node:
        raise PathError(f"'{path}': '{step}' 칸이 없습니다.")
    return node[step]


def get(obj, path: str):
    """경로가 가리키는 값을 돌려준다."""
    node = obj
    for step in parse(path):
        node = _child(node, step, path)
    return node


def put(obj, path: str, value) -> None:
    """경로에 값을 넣는다. 마지막이 `[+]` 면 목록 끝에 더하고, 이름이면 새 칸도 만든다."""
    steps = parse(path)
    parent = obj
    for step in steps[:-1]:
        parent = _child(parent, step, path)
    last = steps[-1]
    if last == APPEND:
        if not isinstance(parent, list):
            raise PathError(f"'{path}': 목록이 아니라 [+] 로 추가할 수 없습니다.")
        parent.append(value)
    elif isinstance(last, int):
        _child(parent, last, path)
        parent[last] = value
    else:
        if not isinstance(parent, dict):
            raise PathError(f"'{path}': '{last}' 를 둘 수 있는 객체가 아닙니다.")
        parent[last] = value


def remove(obj, path: str) -> None:
    """경로의 목록 항목이나 칸을 지운다."""
    steps = parse(path)
    if steps[-1] == APPEND:
        raise PathError(f"'{path}': 지울 위치를 정확히 쓰세요.")
    parent = obj
    for step in steps[:-1]:
        parent = _child(parent, step, path)
    _child(parent, steps[-1], path)
    del parent[steps[-1]]


def leaves(obj, prefix: str = "") -> list[tuple[str, object]]:
    """끝값(문자열·숫자·불리언·null)과 빈 목록·객체를 `(경로, 값)` 으로 펼친다."""
    out: list[tuple[str, object]] = []
    if isinstance(obj, dict) and obj:
        for key, value in obj.items():
            out += leaves(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(obj, list) and obj:
        for i, value in enumerate(obj):
            out += leaves(value, f"{prefix}[{i}]")
    else:
        out.append((prefix, obj))
    return out


def show(value) -> str:
    """보기 화면의 값 표기. 문자열은 그대로, 나머지는 JSON 으로."""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
