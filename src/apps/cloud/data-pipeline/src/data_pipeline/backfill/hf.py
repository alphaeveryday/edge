"""HuggingFace 공개 데이터셋 접근 — **재구축의 유일한 외부 입력.**

`dartlab` 패키지를 쓰지 않는다. 그 패키지는 다운로드·캐시·CLI·AI 에이전트를 함께 들고
있고, 백필이 그런 런타임에 매이면 **재구축 가능성이 그 패키지의 수명에 매인다.** 우리가
쓰는 것은 공개 parquet 파일 하나뿐이므로 URL 로 직접 읽는다(의존은 표준 라이브러리 +
pyarrow, 둘 다 이미 있다).

파일 목록도 여기서 얻는다. 종목 유니버스를 로컬 마스터에서 읽으면 그 마스터가 사라졌을 때
같은 백필을 재현할 수 없다 - **데이터셋이 유니버스의 정의**가 되게 둔다.

`oid`(git blob sha)를 매니페스트에 남긴다. 내용 해시이므로 나중에 "그때 그 파일과 같은
것인가"를 대조할 수 있다 - HF 의 `main` 은 움직이는 참조라 시각만으로는 고정되지 않는다.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

API = "https://huggingface.co/api/datasets"
RESOLVE = "https://huggingface.co/datasets"
UA = {"User-Agent": "edge-data-pipeline-backfill/0.1"}
_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


class HfError(RuntimeError):
    """데이터셋 접근 실패. **조용히 빈 목록을 내지 않는다** - 백필이 조용히 0건이 된다."""


@dataclass(frozen=True)
class HfFile:
    """데이터셋 파일 하나 — oid(git blob sha)가 내용 정체다."""

    path: str
    size: int
    oid: str          # git blob sha — 내용 정체


@dataclass
class HfDataset:
    """공개 데이터셋 한 개. `repo` 와 `revision` 이 재현의 좌표다."""

    repo: str = "eddmpython/dartlab-data"
    revision: str = "main"
    timeout: int = 120
    _cache: dict[str, list[HfFile]] = field(default_factory=dict)

    def _get(self, url: str) -> tuple[bytes, dict]:
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read(), dict(r.headers)
        except urllib.error.HTTPError as exc:
            raise HfError(f"{exc.code} {url}") from exc
        except OSError as exc:
            raise HfError(f"{type(exc).__name__} {url}: {exc}") from exc

    def files(self, folder: str) -> list[HfFile]:
        """폴더의 파일 전량. **페이지를 끝까지 따라간다** - 1000개에서 잘리면 유니버스가 준다."""
        if folder in self._cache:
            return self._cache[folder]
        url = f"{API}/{self.repo}/tree/{self.revision}/{folder}"
        out: list[HfFile] = []
        while url:
            body, headers = self._get(url)
            for item in json.loads(body):
                if item.get("type") == "file":
                    out.append(HfFile(path=item["path"], size=int(item.get("size") or 0),
                                      oid=str(item.get("oid") or "")))
            nxt = _NEXT.search(headers.get("Link", "") or "")
            url = nxt.group(1) if nxt else ""
        if not out:
            raise HfError(f"{folder} 에 파일이 없다 - 경로나 리비전이 틀렸다")
        self._cache[folder] = out
        return out

    def tickers(self, folder: str) -> list[str]:
        """유니버스. `.parquet` 만 세고 `.arrow` 미러는 뺀다(같은 내용의 다른 포맷)."""
        return sorted({f.path.rsplit("/", 1)[-1][:-len(".parquet")]
                       for f in self.files(folder) if f.path.endswith(".parquet")})

    def fetch(self, path: str) -> bytes:
        """파일 원본 바이트 — 실패는 HfError."""
        return self._get(f"{RESOLVE}/{self.repo}/resolve/{self.revision}/{path}")[0]

    def oid_of(self, path: str) -> str:
        """path 의 현재 oid — 목록에 없으면 HfError."""
        folder = path.rsplit("/", 1)[0]
        for f in self.files(folder):
            if f.path == path:
                return f.oid
        raise HfError(f"{path} 가 목록에 없다")
