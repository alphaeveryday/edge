"""세션 작업 폴더. 도구 호출마다 프로세스가 새로 뜨므로 상태는 모두 파일에 둔다.

    case.json      지금 고치는 사건 (discover 는 create_case 전까지 없음)
    base.json      세션 시작 때 사건 (update) — 읽기 전용 칸 비교용
    inbox.json     이번 실행에서 배정된 기사 (update) 또는 후보 기사 (discover)
    index.json     다른 사건 요약 (중복 확인용) — plan 세션에서는 조사 후보 사건 목록
    notes.md       이 사건의 조사 노트 (update) — 세션은 자기 사건 노트 하나만 받는다
    state.json     본 버전·읽은 출처·웹 호출 수·판정 제안·완료 여부
    snapshots/     웹에서 가져온 원문 (인용 근거)
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import time


class Workspace:
    """세션 폴더 한 개."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.snapshots = self.root / "snapshots"

    # ── 준비 (runner 가 세션 전에 한 번) ──
    @classmethod
    def prepare(cls, root: Path, *, mode: str, case: dict | None, inbox: list[dict], index: list[dict],
                today: str, now: str, notes: str | None = None) -> "Workspace":
        """세션 폴더를 만든다."""
        ws = cls(root)
        ws.root.mkdir(parents=True, exist_ok=True)
        ws.snapshots.mkdir(exist_ok=True)
        if case is not None:
            ws._write("case.json", case)
            ws._write("base.json", case)
        if notes is not None:
            (ws.root / "notes.md").write_text(notes, encoding="utf-8")
        ws._write("inbox.json", inbox)
        ws._write("index.json", index)
        ws._write("state.json", {"mode": mode, "today": today, "now": now, "viewedHash": None, "viewed": [],
                                 "webCalls": 0, "verdict": None, "notesWritten": False, "plan": None,
                                 "done": False, "summary": ""})
        return ws

    # ── 파일 ──
    def _read(self, name: str, default=None):
        path = self.root / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default

    def _write(self, name: str, value) -> None:
        path = self.root / name
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    @contextmanager
    def lock(self, timeout: float = 30.0):
        """한 세션 안의 병렬 도구 호출이 같은 파일을 동시에 고치지 않게 한다."""
        path = self.root / ".lock"
        deadline = time.monotonic() + timeout
        while True:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                if time.monotonic() > deadline:
                    raise TimeoutError("작업 폴더 잠금을 얻지 못했습니다.")
                time.sleep(0.05)
        try:
            yield
        finally:
            os.close(fd)
            path.unlink(missing_ok=True)

    @property
    def state(self) -> dict:
        return self._read("state.json")

    def save_state(self, state: dict) -> None:
        self._write("state.json", state)

    def case(self) -> dict | None:
        return self._read("case.json")

    def base(self) -> dict | None:
        return self._read("base.json")

    def save_case(self, case: dict) -> str:
        """사건을 쓰고 새 버전 해시를 돌려준다."""
        self._write("case.json", case)
        return self.case_hash()

    def case_hash(self) -> str | None:
        path = self.root / "case.json"
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

    def inbox(self) -> list[dict]:
        return self._read("inbox.json", [])

    def index(self) -> list[dict]:
        return self._read("index.json", [])

    def notes(self) -> str | None:
        path = self.root / "notes.md"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def save_notes(self, text: str) -> None:
        tmp = self.root / "notes.md.tmp"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.root / "notes.md")

    def snapshot(self, sid: str) -> dict | None:
        return self._read(f"snapshots/{sid}.json")

    def save_snapshot(self, sid: str, value: dict) -> None:
        self._write(f"snapshots/{sid}.json", value)
