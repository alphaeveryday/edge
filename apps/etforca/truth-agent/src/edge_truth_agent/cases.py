"""사건 파일 계약: JSON 스키마 검사와 스키마로 표현할 수 없는 의미 규칙.

오류 문장은 사람과 에이전트가 같이 읽는다. 무엇이 틀렸는지와 다음에 무엇을 하면 되는지를
함께 적는다(에이전트는 이 문장만 보고 고친다).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from importlib import resources
import json
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA = json.loads(resources.files(__package__).joinpath("case.schema.json").read_text(encoding="utf-8"))
_VALIDATOR = Draft202012Validator(SCHEMA)

# 판정(밝혀짐·해소)은 공식 판단이나 법정공시가 있을 때만 바꾼다 (방법론 §8 확인 수준).
RESOLUTION_KINDS = frozenset({"official", "disclosure"})
# 공개 문장에서 확인 수준을 넘어서는 단정어. 행위를 구체적으로 쓰게 한다 (방법론 §8 작성 원칙).
BANNED_HEADLINE_WORDS = ("거짓말", "사기꾼")


class CaseError(ValueError):
    """사건 파일이 계약을 어겼다."""


def _path(error) -> str:
    """jsonschema 오류 위치를 `behaviors[0].basis` 형태로 바꾼다."""
    out = ""
    for part in error.absolute_path:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or "(사건 전체)"


def _day(value: str, where: str, problems: list[str]) -> date | None:
    """YYYY-MM-DD 를 읽고, 틀리면 문제 목록에 적는다."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        problems.append(f"{where}: '{value}' 는 날짜가 아닙니다. YYYY-MM-DD 로 쓰세요.")
        return None


def problems(case: dict, *, file_id: str | None = None, today: date | None = None) -> list[str]:
    """사건 하나의 계약 위반 목록을 돌려준다. 빈 목록이면 통과다.

    Args:
        case: 사건 JSON.
        file_id: 파일 이름(확장자 제외). 주어지면 case id 와 같아야 한다.
        today: 미래 날짜 판정 기준일. 생략하면 오늘.

    Returns:
        사람이 읽는 위반 문장 목록.
    """
    shape = sorted(_VALIDATOR.iter_errors(case), key=lambda e: list(e.absolute_path))
    if shape:
        return [f"{_path(e)}: {e.message}" for e in shape]

    found: list[str] = []
    today = today or date.today()
    latest = today + timedelta(days=1)  # 시간대 차이로 하루 앞선 날짜는 허용한다

    if file_id is not None and case["id"] != file_id:
        found.append(f"id: 파일 이름은 '{file_id}' 인데 id 는 '{case['id']}' 입니다. 둘을 같게 맞추세요.")

    sources = {s["id"]: s for s in case["sources"]}
    if len(sources) != len(case["sources"]):
        found.append("sources: 같은 id 의 출처가 두 번 있습니다. 하나로 합치세요.")

    def need(source_id: str, where: str) -> dict | None:
        if source_id not in sources:
            found.append(f"{where}: '{source_id}' 는 sources 에 없는 출처입니다. sources 에 먼저 추가하거나 있는 id 를 쓰세요.")
            return None
        return sources[source_id]

    for i, b in enumerate(case["behaviors"]):
        for j, sid in enumerate(b["sourceIds"]):
            need(sid, f"behaviors[{i}].sourceIds[{j}]")
    for side in ("before", "after"):
        need(case["contrast"][side]["sourceId"], f"contrast.{side}.sourceId")
    for i, r in enumerate(case["rebuttal"]):
        need(r["sourceId"], f"rebuttal[{i}].sourceId")
    for i, block in enumerate(case["body"]):
        for j, sid in enumerate(block.get("sourceIds", [])):
            need(sid, f"body[{i}].sourceIds[{j}]")
    for i, r in enumerate(case["related"]):
        need(r["sourceId"], f"related[{i}].sourceId")

    status, resolution = case["status"], case.get("resolution")
    if status == "pending":
        if resolution or "resolvedAt" in case:
            found.append("status: 판단 중(pending) 사건에는 resolution·resolvedAt 을 두지 않습니다.")
    else:
        if not resolution or "resolvedAt" not in case:
            found.append(f"status: '{status}' 로 바꾸려면 resolution 과 resolvedAt 이 필요합니다.")
        else:
            src = need(resolution["sourceId"], "resolution.sourceId")
            if src and src["kind"] not in RESOLUTION_KINDS:
                found.append(
                    f"resolution.sourceId: '{src['id']}' 는 {src['kind']} 출처입니다. 판정은 공식 판단(official)이나 "
                    "법정공시(disclosure) 출처로만 바꿀 수 있습니다. 그런 출처가 없으면 status 를 pending 으로 두세요."
                )
            if resolution["at"] != case["resolvedAt"]:
                found.append("resolvedAt: resolution.at 과 같은 날짜여야 합니다.")
        wanted = "confirmed" if status == "confirmed" else "refuted"
        if case["levels"]["behavior"] != wanted:
            found.append(f"levels.behavior: status 가 '{status}' 이면 행동 확인 수준도 '{wanted}' 여야 합니다.")
        if not any(b["verdict"] == wanted for b in case["behaviors"]):
            found.append(f"behaviors: status 가 '{status}' 이면 판정이 '{wanted}' 인 유형이 하나 이상 있어야 합니다.")

    for where, value in [("flaggedAt", case["flaggedAt"]), ("resolvedAt", case.get("resolvedAt")),
                         ("resolution.at", (resolution or {}).get("at"))]:
        if value is not None and (day := _day(value, where, found)) and day > latest:
            found.append(f"{where}: {value} 는 미래 날짜입니다.")
    for i, s in enumerate(case["sources"]):
        for key in ("publishedAt", "accessedAt"):
            if (day := _day(s[key], f"sources[{i}].{key}", found)) and day > latest:
                found.append(f"sources[{i}].{key}: {s[key]} 는 미래 날짜입니다.")
    try:
        if datetime.fromisoformat(case["updatedAt"]).tzinfo is None:
            found.append("updatedAt: 시간대를 붙이세요 (예: 2026-10-20T09:00:00+09:00).")
    except ValueError:
        found.append(f"updatedAt: '{case['updatedAt']}' 는 날짜·시각이 아닙니다.")

    for i, sentence in enumerate(case["headline"]):
        for word in BANNED_HEADLINE_WORDS:
            if word in sentence:
                found.append(f"headline[{i}]: '{word}' 대신 확인된 행위를 구체적으로 쓰세요 (예: '…라고 발표했다', '공시에는 …').")
    return found


def validate(case: dict, **kwargs) -> dict:
    """계약을 어기면 CaseError 를 낸다."""
    if found := problems(case, **kwargs):
        raise CaseError("\n".join(found))
    return case


def load_dir(directory: Path) -> list[dict]:
    """폴더의 사건 파일을 모두 읽고 검사한다. 하나라도 틀리면 파일별 위반을 모아 CaseError 를 낸다."""
    cases, errors = [], []
    for path in sorted(Path(directory).glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        if found := problems(case, file_id=path.stem):
            errors.extend(f"{path.name} {line}" for line in found)
        cases.append(case)
    if errors:
        raise CaseError("\n".join(errors))
    return cases
