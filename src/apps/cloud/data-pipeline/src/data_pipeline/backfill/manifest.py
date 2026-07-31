"""백필 매니페스트 — **재개·검증·재구축의 근거. 레이크에 쓴다.**

로컬 디스크에 두지 않는다. 매니페스트가 로컬에 있으면 그 디스크가 사라졌을 때 재개가
불가능하고, "데이터가 전소해도 다시 쌓을 수 있다"는 요구가 깨진다. 레이크에 두면 백필
러너를 어디서 다시 띄워도 이어서 돌 수 있다.

무엇을 남기나. **입력의 정체와 산출의 정체**다.

    input   repo · revision · folder · 티커별 oid(git blob sha)
    output  레이크 키 · 행 수 · sha256
    run     run_id · ingest_date · 시작·종료 시각 · 코드 버전

이게 있으면 세 질문에 답할 수 있다: 어디까지 했나(재개) · 쌓인 것이 그때 그것인가(검증) ·
같은 것을 다시 만들 수 있나(재구축). 셋 중 하나라도 못 하면 백필이 아니라 일회성 적재다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

MANIFEST_VERSION = 1


def manifest_key(source: str, dataset: str, run_id: str, prefix: str = "") -> str:
    """매니페스트 키. `collection_log_key` 와 같은 존(operations_archive)에 둔다.

    `prefix`(`draft/`)를 받는 이유: 초안 실행의 매니페스트가 프로덕션 경로에 쓰이면
    같은 run_id 로 서로를 덮는다. **격리는 데이터 파티션만이 아니라 원장에도 필요하다.**
    """
    key = (f"operations_archive/backfill_manifests/source={source}"
           f"/dataset={dataset}/run_id={run_id}/manifest.json")
    return f"{prefix.rstrip('/')}/{key}" if prefix else key


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Manifest:
    """한 백필 실행의 원장. 항목은 티커 단위다."""

    source: str
    dataset: str
    market: str
    run_id: str
    ingest_date: str
    repo: str
    revision: str
    folder: str
    prefix: str = ""          # draft/ 등 쓰기 접두사. 원장도 같은 곳에 살아야 한다
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str = ""
    version: int = MANIFEST_VERSION
    items: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ── 왕복 ────────────────────────────────────────────────────────
    def to_bytes(self) -> bytes:
        return json.dumps(self.__dict__, ensure_ascii=False,
                          indent=1, sort_keys=True).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> Manifest:
        raw = json.loads(data)
        got = raw.pop("version", 0)
        if got != MANIFEST_VERSION:
            # 조용히 읽으면 옛 형식을 새 규칙으로 해석해 재개가 어긋난다.
            raise ValueError(f"매니페스트 version={got} (지원 {MANIFEST_VERSION})")
        return cls(version=MANIFEST_VERSION, **raw)

    @classmethod
    def load_or_new(cls, storage, **kw) -> Manifest:
        """있으면 이어 쓰고 없으면 새로. **재개가 기본값이다.**"""
        key = manifest_key(kw["source"], kw["dataset"], kw["run_id"],
                           kw.get("prefix", ""))
        try:
            return cls.from_bytes(storage.get_bytes(key))
        except Exception:  # noqa: BLE001 - 없거나 못 읽으면 새로 시작한다
            return cls(**kw)

    def save(self, storage) -> str:
        key = manifest_key(self.source, self.dataset, self.run_id, self.prefix)
        storage.put_bytes(key, self.to_bytes())
        return key

    # ── 항목 ────────────────────────────────────────────────────────
    def record(self, ticker: str, *, oid: str, key: str, rows: int,
               digest: str, bytes_out: int) -> None:
        self.items[ticker] = {"oid": oid, "key": key, "rows": rows,
                              "sha256": digest, "bytes": bytes_out,
                              "at": datetime.now(UTC).isoformat()}

    def fail(self, ticker: str, error: str) -> None:
        """실패도 남긴다. **빠진 것과 실패한 것은 다르다** - 세지 않으면 결손이 숨는다."""
        self.items[ticker] = {"error": error[:300],
                              "at": datetime.now(UTC).isoformat()}

    def done(self, ticker: str, *, oid: str = "") -> bool:
        """이미 받았나. `oid` 를 주면 **내용까지** 같은지 본다 - 업스트림이 바뀌면 다시 받는다."""
        it = self.items.get(ticker)
        if not it or it.get("error"):
            return False
        return not oid or it.get("oid") == oid

    @property
    def ok(self) -> list[str]:
        return sorted(t for t, v in self.items.items() if not v.get("error"))

    @property
    def failed(self) -> list[str]:
        return sorted(t for t, v in self.items.items() if v.get("error"))

    @property
    def rows(self) -> int:
        return sum(int(v.get("rows") or 0) for v in self.items.values())

    def close(self) -> None:
        self.finished_at = datetime.now(UTC).isoformat()

    # ── 검증 ────────────────────────────────────────────────────────
    def verify(self, storage) -> dict:
        """레이크의 실제 객체가 매니페스트와 같은가. **쌓였다는 말을 믿지 않는다.**"""
        bad: list[dict] = []
        for ticker, it in sorted(self.items.items()):
            if it.get("error"):
                continue
            try:
                got = sha256(storage.get_bytes(it["key"]))
            except Exception as exc:  # noqa: BLE001
                bad.append({"ticker": ticker, "why": f"읽기 실패 {type(exc).__name__}"})
                continue
            if got != it["sha256"]:
                bad.append({"ticker": ticker, "why": "sha256 불일치",
                            "expected": it["sha256"][:12], "actual": got[:12]})
        return {"checked": len(self.ok), "mismatched": len(bad), "bad": bad[:20],
                "failed_items": len(self.failed), "rows": self.rows}
