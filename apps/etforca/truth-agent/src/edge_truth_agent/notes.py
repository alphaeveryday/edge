"""사건별 조사 노트(md). 비공개 저장소 notes/<사건 id>.md 에 둔다.

세션 하나는 자기 사건의 노트 하나만 받는다. 모델은 write_notes 로 '다음에 찾아볼 것'(통째 교체)과
이번 조사 기록(한 항목)만 넘기고, 파일 형식·지난 기록 보존·길이 제한은 여기 코드가 맡는다.
"""
from __future__ import annotations

import re

NEXT, LOG = "## 다음에 찾아볼 것", "## 조사 기록"
KEEP_RECORDS = 8      # 조사 기록은 최근 이만큼만 남긴다(세션에 싣는 노트 길이를 묶는다)
MAX_ITEMS, MAX_ITEM, MAX_RECORD = 8, 200, 800


def key(case_id: str) -> str:
    return f"notes/{case_id}.md"


def template(case: dict) -> str:
    return render(case.get("title") or case["headline"][0], [], [])


def render(title: str, items: list[str], records: list[str]) -> str:
    todo = "\n".join(f"- [ ] {i}" for i in items) or "- (없음)"
    log = "\n\n".join(records) or "(아직 없음)"
    return f"# {title} 조사 노트\n\n{NEXT}\n{todo}\n\n{LOG}\n{log}\n"


def _parse(text: str) -> tuple[str, list[str], list[str]]:
    """(제목, 할 일, 조사 기록 항목들). 기록 항목은 '### 시각' 로 시작한다(최신이 위)."""
    head, _, rest = text.partition(NEXT)
    title = head.strip().removeprefix("# ").removesuffix(" 조사 노트") or "사건"
    todo, _, log = rest.partition(LOG)
    items = [line[6:].strip() for line in todo.splitlines() if line.startswith("- [ ] ")]
    records = [r.strip() for r in re.split(r"\n(?=### )", log.strip()) if r.startswith("### ")]
    return title, items, records


def items(text: str) -> list[str]:
    return _parse(text)[1]


def open_items(text: str) -> int:
    return len(items(text))


def update(text: str, when: str, items: list[str], record: str) -> str:
    """할 일을 통째로 바꾸고, 이번 시각의 기록을 맨 위에 둔다(같은 시각 기록은 덮는다). 오래된 기록은 버린다."""
    title, _, records = _parse(text)
    head = f"### {when}"
    record = "\n".join(line.lstrip("#").strip() if line.startswith("#") else line for line in record.strip().splitlines())  # 기록 안의 제목줄이 항목을 쪼개지 않게
    records = [r for r in records if r.split("\n", 1)[0] != head]
    return render(title, items, [f"{head}\n{record.strip()}"] + records[:KEEP_RECORDS - 1])


def add_record(text: str, when: str, record: str) -> str:
    """할 일은 그대로 두고 기록만 남긴다(세션이 마무리하지 못했을 때 코드가 쓴다)."""
    return update(text, when, _parse(text)[1], record)


def problems(items: list, record: str) -> list[str]:
    found = []
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        found.append(f"next 는 할 일 문장 목록이며 {MAX_ITEMS}개까지입니다. 가장 중요한 것만 남기세요.")
    elif any(not isinstance(i, str) or not i.strip() or len(i) > MAX_ITEM or "\n" in i for i in items):
        found.append(f"next 의 각 항목은 한 줄, {MAX_ITEM}자 이내 문장입니다(무엇을 어디서 확인할지).")
    if not record.strip() or len(record) > MAX_RECORD:
        found.append(f"record 는 이번에 확인한 것·바꾼 것·막힌 것을 {MAX_RECORD}자 안에 적습니다.")
    return found
