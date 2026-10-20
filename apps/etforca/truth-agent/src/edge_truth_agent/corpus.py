"""검색 말뭉치: 최근 기사(제목·요약)를 보관하고 에이전트의 내부 뉴스 검색에 쓴다.

비공개 저장소에 `state/corpus.jsonl.gz` 로 두고, 실행마다 새 기사를 더하고 오래된 기사를 뺀다.
세션은 실행이 만든 SQLite 사본을 읽기만 한다(도구 호출마다 프로세스가 새로 떠도 빠르게).
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, timedelta
import gzip
import json
from pathlib import Path
import sqlite3

from .news import Article

CORPUS_KEY = "state/corpus.jsonl.gz"


def load(store) -> dict[str, Article]:
    """저장된 말뭉치. 없으면 빈 말뭉치."""
    data, _ = store.get(CORPUS_KEY)
    if data is None:
        return {}
    out = {}
    for line in gzip.decompress(data).decode("utf-8").splitlines():
        if line:
            a = Article(**json.loads(line))
            out[a.id] = a
    return out


def save(store, corpus: dict[str, Article]) -> None:
    """말뭉치를 저장한다 (발행일 내림차순, 같은 입력이면 같은 바이트)."""
    rows = sorted(corpus.values(), key=lambda a: (a.published, a.id), reverse=True)
    body = "\n".join(json.dumps(a.to_dict(), ensure_ascii=False, sort_keys=True) for a in rows).encode("utf-8")
    store.put(CORPUS_KEY, gzip.compress(body, mtime=0), content_type="application/gzip")


def prune(corpus: dict[str, Article], today: date, days: int) -> dict[str, Article]:
    """발행일이 days 일보다 오래된 기사를 뺀다. 발행일을 모르는 기사는 남긴다."""
    oldest = (today - timedelta(days=days)).isoformat()
    return {k: a for k, a in corpus.items() if not a.published or a.published[:10] >= oldest}


def build_index(corpus: dict[str, Article], path: Path) -> Path:
    """세션이 읽을 SQLite 파일을 만든다."""
    path.unlink(missing_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("create table articles (id text primary key, title text, lead text, url text, publisher text, published text)")
        db.executemany("insert into articles values (?,?,?,?,?,?)",
                       [(a.id, a.title, a.lead, a.url, a.publisher, a.published) for a in corpus.values()])
    return path


def search(path: Path, query: str, *, since: str | None = None, limit: int = 15) -> list[Article]:
    """제목·요약에 검색어 낱말이 모두 들어간 기사를 최신순으로. 한국어 조사 때문에 부분 문자열로 찾는다."""
    words = [w for w in query.split() if w]
    if not words:
        return []
    sql = "select id,title,lead,url,publisher,published from articles where " + " and ".join(
        ["(instr(title, ?) > 0 or instr(lead, ?) > 0)"] * len(words))
    args: list[str] = [x for w in words for x in (w, w)]
    if since:
        sql += " and published >= ?"
        args.append(since)
    sql += " order by published desc limit ?"
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        return [Article(*row) for row in db.execute(sql, [*args, limit])]


def find(path: Path, article_id: str) -> Article | None:
    """id(전체 또는 news- 출처 id 의 앞 16자리)로 기사 한 건."""
    key = article_id.removeprefix("news-")
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        row = db.execute("select id,title,lead,url,publisher,published from articles where id = ? or substr(id,1,16) = ? limit 1",
                         (key, key)).fetchone()
    return Article(*row) if row else None
