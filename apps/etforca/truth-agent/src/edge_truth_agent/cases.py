"""사건 파일 계약: JSON 스키마 검사와 스키마로 표현할 수 없는 의미 규칙.

오류 문장은 사람과 에이전트가 같이 읽는다. 무엇이 틀렸는지와 다음에 무엇을 하면 되는지를
함께 적는다(에이전트는 이 문장만 보고 고친다).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from importlib import resources
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

SCHEMA = json.loads(resources.files(__package__).joinpath("case.schema.json").read_text(encoding="utf-8"))
_VALIDATOR = Draft202012Validator(SCHEMA)

# 판정은 공식 출처로만 바꾼다 (방법론 §8 확인 수준).
# - 밝혀짐(confirmed): 공식 판단 또는 법정공시(예: 정정공시로 회사가 인정)
# - 해소(refuted): 공식 판단만. 당사자 해명공시는 반론이지 반증의 입증이 아니다(방법론 §5 '반론의 존재와 입증을 구별').
RESOLUTION_KINDS = frozenset({"official", "disclosure"})
REFUTATION_KINDS = frozenset({"official"})
# 출처 종류를 사건 파일이 스스로 주장하므로, 판정 근거 출처만큼은 주소로 다시 확인한다.
OFFICIAL_HOSTS = ("fsc.go.kr", "fss.or.kr", "spo.go.kr", "police.go.kr", "scourt.go.kr", "ftc.go.kr", "moj.go.kr", "www.krx.co.kr")
DISCLOSURE_HOSTS = ("dart.fss.or.kr", "opendart.fss.or.kr", "kind.krx.co.kr")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# 공개 문장에서 확인 수준을 넘어서는 단정어. 행위를 구체적으로 쓰게 한다 (방법론 §8 작성 원칙).
BANNED_HEADLINE_WORDS = ("거짓말", "사기꾼")
# 짧은 제목은 훑어 읽는 자리라 판정·감정·뭉뚱그린 말이 더 쉽게 단정처럼 읽힌다
BANNED_TITLE_WORDS = BANNED_HEADLINE_WORDS + ("거짓", "기만", "논란", "의혹", "충격", "경악", "폭로", "결국")


class CaseError(ValueError):
    """사건 파일이 계약을 어겼다."""


def _path(error) -> str:
    """jsonschema 오류 위치를 `behaviors[0].basis` 형태로 바꾼다."""
    out = ""
    for part in error.absolute_path:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or "(사건 전체)"


def host_of(url: str) -> str:
    """주소의 호스트(소문자). 해석할 수 없는 주소('https://[x/')는 빈 문자열 — 검사가 예외로 멈추지 않게."""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def host_kind(url: str) -> str | None:
    """주소가 공식 판단(official)·법정공시(disclosure) 기관 도메인이면 그 종류. 공시 도메인을 먼저 본다."""
    if "\\" in url or any(ch.isspace() for ch in url):
        return None  # Python 과 브라우저가 호스트를 다르게 읽는 주소는 공식 출처로 인정하지 않는다
    host = host_of(url)

    def under(domains: tuple[str, ...]) -> bool:
        return any(host == d or host.endswith("." + d) for d in domains)

    if under(DISCLOSURE_HOSTS):
        return "disclosure"
    if under(OFFICIAL_HOSTS):
        return "official"
    return None


def _day(value: str, where: str, problems: list[str]) -> date | None:
    """정확히 YYYY-MM-DD 인 날짜를 읽고, 틀리면 문제 목록에 적는다.

    date.fromisoformat 은 '20261007'·'2026-W41-3' 도 받는다. 화면은 날짜를 문자열로 비교하므로
    형식이 하나가 아니면 순서·적중 판정이 틀어진다.
    """
    try:
        if not _DATE.fullmatch(value):
            raise ValueError(value)
        return date.fromisoformat(value)
    except ValueError:
        problems.append(f"{where}: '{value}' 는 날짜가 아닙니다. YYYY-MM-DD 로 쓰세요.")
        return None


def _moment(value: str, where: str, problems: list[str], latest: date) -> None:
    """시간대가 붙은 날짜·시각이고 미래가 아닌지 본다."""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        problems.append(f"{where}: '{value}' 는 날짜·시각이 아닙니다.")
        return
    if moment.tzinfo is None or not _DATE.match(value):
        problems.append(f"{where}: 시간대를 붙인 날짜·시각으로 쓰세요 (예: 2026-10-20T09:00:00+09:00).")
    elif moment.date() > latest:
        problems.append(f"{where}: {value} 는 미래 시각입니다.")


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
            allowed = REFUTATION_KINDS if status == "refuted" else RESOLUTION_KINDS
            if src and src["kind"] not in allowed:
                found.append(
                    f"resolution.sourceId: '{src['id']}' 는 {src['kind']} 출처입니다. "
                    + ("해소(refuted)는 공식 판단(official) 출처로만 정합니다. 당사자 해명은 반론에 적으세요."
                       if status == "refuted" else
                       "밝혀짐(confirmed)은 공식 판단(official)이나 법정공시(disclosure) 출처로만 정합니다.")
                    + " 그런 출처가 없으면 status 를 pending 으로 두세요."
                )
            elif src and host_kind(src["url"]) not in allowed:
                found.append(f"resolution.sourceId: '{src['id']}' 주소({src['url']})가 공식 기관·공시 도메인이 아닙니다. "
                             "판정 근거는 기관 원문 주소여야 합니다.")
            if src and any(r["sourceId"] == src["id"] for r in case["rebuttal"]):
                found.append(f"resolution.sourceId: '{src['id']}' 는 당사자 반론으로 인용된 출처입니다. 반론을 판정 근거로 쓸 수 없습니다.")
            if resolution["at"] != case["resolvedAt"]:
                found.append("resolvedAt: resolution.at 과 같은 날짜여야 합니다.")
            elif src and _DATE.fullmatch(resolution["at"]) and _DATE.fullmatch(src["publishedAt"]) and resolution["at"] != src["publishedAt"]:
                found.append(f"resolution.at: {resolution['at']} 가 근거 출처 발행일({src['publishedAt']})과 다릅니다. "
                             "확인일은 그 판단·공시가 나온 날입니다(찾은 날도, 근거 없는 더 이른 날도 아닙니다).")
        wanted = "confirmed" if status == "confirmed" else "refuted"
        if case["levels"]["behavior"] != wanted:
            found.append(f"levels.behavior: status 가 '{status}' 이면 행동 확인 수준도 '{wanted}' 여야 합니다.")
        if not any(b["verdict"] == wanted for b in case["behaviors"]):
            found.append(f"behaviors: status 가 '{status}' 이면 판정이 '{wanted}' 인 유형이 하나 이상 있어야 합니다.")
        elif resolution and not any(b["verdict"] == wanted and resolution["sourceId"] in b["sourceIds"] for b in case["behaviors"]):
            # 화면의 '유형별 판정'은 유형의 출처만 잇는다 — 판정 근거가 거기 없으면 공식 근거 없이 판정이 붙어 보인다.
            found.append(f"behaviors: 판정이 '{wanted}' 인 유형 중 하나는 판정 근거({resolution['sourceId']})를 sourceIds 에 담아야 합니다.")

    accessed = [s["accessedAt"] for s in case["sources"] if _DATE.fullmatch(s["accessedAt"])]
    if accessed and _DATE.fullmatch(case["flaggedAt"]) and case["flaggedAt"] < min(accessed):
        found.append(f"flaggedAt: {case['flaggedAt']} 는 가장 이른 출처 접근일({min(accessed)})보다 이릅니다. "
                     "출처를 보기 전에 사건으로 기록할 수는 없습니다(적중률 부풀림 방지).")
    for where, value in [("flaggedAt", case["flaggedAt"]), ("resolvedAt", case.get("resolvedAt")),
                         ("resolution.at", (resolution or {}).get("at"))]:
        if value is not None and (day := _day(value, where, found)) and day > latest:
            found.append(f"{where}: {value} 는 미래 날짜입니다.")
    for i, s in enumerate(case["sources"]):
        if not host_of(s["url"]):
            found.append(f"sources[{i}].url: '{s['url']}' 에서 호스트를 읽을 수 없습니다. 원문 주소를 그대로 쓰세요.")
        for key in ("publishedAt", "accessedAt"):
            if (day := _day(s[key], f"sources[{i}].{key}", found)) and day > latest:
                found.append(f"sources[{i}].{key}: {s[key]} 는 미래 날짜입니다.")
        if _DATE.fullmatch(s["publishedAt"]) and _DATE.fullmatch(s["accessedAt"]) and s["accessedAt"] < s["publishedAt"]:
            found.append(f"sources[{i}].accessedAt: {s['accessedAt']} 는 발행일({s['publishedAt']})보다 이릅니다. "
                         "나오기 전의 출처를 볼 수는 없습니다(기록일 소급 방지).")
    _moment(case["updatedAt"], "updatedAt", found, latest)
    # 갱신 시각은 기록이 마지막으로 바뀐 때다 — 기록 안의 어떤 날짜(기록일·확인일·출처 접근일·관련 보도 추가)보다도 이를 수 없다.
    inside = [case["flaggedAt"], case.get("resolvedAt") or ""]
    inside += [s["accessedAt"] for s in case["sources"]] + [r["addedAt"][:10] for r in case["related"]]
    newest = max((d for d in inside if _DATE.fullmatch(d[:10])), default="")[:10]
    if newest and case["updatedAt"][:10] < newest:
        found.append(f"updatedAt: {case['updatedAt']} 는 기록 안의 가장 늦은 날짜({newest})보다 이릅니다.")
    for i, r in enumerate(case["related"]):
        _moment(r["addedAt"], f"related[{i}].addedAt", found, latest)
    if not math.isfinite(case["impact"]):
        found.append("impact: 0~100 사이 숫자여야 합니다.")

    if title := case.get("title"):
        names = [case["subject"], *(i["name"] for i in case["instruments"]), *case.get("aliases", [])]
        if not any(n and n in title for n in names):
            found.append(f"title: '{title}' 에 주체 이름(subject·종목명·별칭 중 하나)이 없습니다. 누구의 사건인지 제목만 보고 알아야 합니다.")
        if title.rstrip()[-1:] in (".", "?", "!", "…"):
            found.append("title: 문장이 아니라 제목입니다. 끝에 마침표·물음표·느낌표를 쓰지 않습니다.")
        for word in BANNED_TITLE_WORDS:
            if word in title:
                found.append(f"title: '{word}' 대신 무슨 일인지 구체적으로 쓰세요 (예: '공개매수 직후 2.5조 증자 발표').")
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
    paths = sorted(Path(directory).glob("*.json"))
    if not paths:  # 경로 오타·빈 마운트를 '0건 통과'로 넘기면 빈 목록이 게시돼 기존 사건이 지워진다
        raise CaseError(f"{directory} 에 사건 파일이 없습니다. 경로를 확인하세요.")
    cases, errors = [], []
    for path in paths:
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            errors.append(f"{path.name} 읽을 수 없는 JSON 입니다 ({error}). 문법을 고치세요.")
            continue
        if found := problems(case, file_id=path.stem):
            errors.extend(f"{path.name} {line}" for line in found)
        cases.append(case)
    if errors:
        raise CaseError("\n".join(errors))
    return cases
