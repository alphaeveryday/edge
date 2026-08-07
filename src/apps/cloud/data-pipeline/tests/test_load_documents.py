"""load_documents 스텝 테스트 — canonical 뉴스 → document (ALPHA-374).

실 DB 없이 돈다 — 가짜 커넥션이 실행된 SQL·파라미터를 기록해 '무엇을 어떻게 넣는가'를 검사한다.
(레포의 다른 테스트도 전부 순수라 CI 에 Postgres 가 없다. 실 RDS e2e 는 수동 검증.)

각 테스트는 **왜 그 동작이 중요한지**를 검사한다: 멱등이 깨지면 재실행이 document_id 를 바꿔
그 문서를 참조할 assertion FK(ALPHA-376)가 전부 끊기고, 자연키 결손 행을 넣으면 같은 기사가
매 런 새 행으로 쌓인다.
"""

import io
import json
from datetime import datetime, timezone

from data_pipeline.config import DbConfig
from data_pipeline.lake import LocalStorage, canonical_news_articles_partition
from data_pipeline.steps import load_documents

_COLUMNS = ("article_id", "source_vendor", "market", "title", "url", "normalized_url",
            "normalized_url_hash", "published_at", "publisher", "lead_text", "mentions",
            "fetched_at")


def _instant(text: str) -> datetime:
    """ISO 문자열을 절대 시각으로 — Postgres 의 timestamptz 비교와 같은 축을 만든다.
    naive 는 UTC 로 본다(수집 어댑터가 전부 aware UTC 를 내므로 실무상 안 나오지만,
    여기서 조용히 다른 축을 비교하느니 명시한다)."""
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _write_canonical(storage, language: str, date: str, rows: list[dict],
                     part: str = "part-00000") -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema([(c, pa.string()) for c in _COLUMNS])
    table = pa.Table.from_pylist([{c: r.get(c) for c in _COLUMNS} for r in rows], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(
        f"{canonical_news_articles_partition(language, date)}/{part}.parquet", buf.getvalue())


def _article(article_id: str, **over) -> dict:
    row = {"article_id": article_id, "source_vendor": "bigkinds", "market": "KR",
           "title": "삼성전자 신규 수주", "url": f"https://news.example/{article_id}",
           "published_at": "2026-07-15T09:00:00+09:00", "publisher": "매일경제",
           "fetched_at": "2026-07-15T01:00:00+00:00"}
    row.update(over)
    return row


_ABSENT = object()   # "행이 없다" 와 "lead_text 가 NULL 이다" 를 가른다


class _FakeCursor:
    """ON CONFLICT DO NOTHING 시맨틱 + `document` 자연키→id 해석 흉내.

    news_document 는 id 를 계산값이 아니라 `SELECT document_id FROM document` 로 얻는다
    (ALPHA-628). 픽스처가 그 해석을 흉내내지 않으면 회귀 테스트가 정작 그 경로를 안 밟아
    갈린 id 를 초록으로 통과시킨다.
    """

    def __init__(self, conn):
        self._conn = conn
        self.rowcount = 1

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self._conn.log.append((flat, params))
        # 괄호 주변 공백은 의미가 없으므로 정규화한다 — 아래 WHERE 전문 동등 비교가
        # 무해한 재배치에 깨지지 않게. 결합자·괄호·절 순서 민감도는 **의도한 것**이다.
        head = flat.upper().replace("( ", "(").replace(" )", ")")
        if head.startswith("INSERT INTO DOCUMENT"):
            self._conn.document_inserts += 1
            if (self._conn.fail_after is not None
                    and self._conn.document_inserts > self._conn.fail_after):
                raise RuntimeError("DB 가 런 도중 터졌다")   # 커밋 경계가 런 전체 = 전량 롤백
            key = (params[1], params[2])
            self.rowcount = 0 if key in self._conn.documents else 1
            self._conn.documents.setdefault(key, params[0])   # DO NOTHING = 기존 id 가 남는다
        elif head.startswith("INSERT INTO NEWS_DOCUMENT"):
            # lead_text 와 publisher(ALPHA-695)는 같은 UPSERT 패턴의 **별도 문**이다 —
            # 컬럼명으로 갈라 각자의 저장소에 쌓는다. 한 저장소에 섞으면 한쪽 회귀가
            # 다른 쪽 단언을 초록으로 가린다.
            column = "publisher" if "PUBLISHER)" in head else "lead_text"
            if "DO UPDATE" in head:
                # UPSERT 의 SET 컬럼이 INSERT 컬럼과 갈리면(복붙 오류) 실제 PG 는 충돌
                # 행에서 엉뚱한 컬럼을 덮는데, 컬럼별 저장소만 보는 픽스처는 그걸 초록으로
                # 가린다 — 여기서 터뜨려 이유를 말한다.
                assert f"SET {column.upper()} = EXCLUDED.{column.upper()}" in head, (
                    f"UPSERT SET 컬럼이 INSERT 컬럼({column})과 다르다: {flat}")
            # 계산값을 그대로 넣는 옛 형태(VALUES)도 해석한다 — 그쪽으로 되돌아가면 픽스처가
            # 터지는 대신 **갈린 id 가 값으로 드러나** 회귀 테스트가 실패 이유를 말해 준다.
            # ALPHA-696 승자 축은 **리드 문에만** 붙는다. publisher 는 같은 UPSERT 패턴의
            # 별도 축이고 이 가드가 다스린 적이 없다 — 붙이면 무관한 컬럼의 시각으로
            # ALPHA-695 승격이 막힌다. 구조로 못박는다(값으로는 안 드러난다).
            observed = None
            if column == "publisher":
                assert "LEAD_OBSERVED_AT" not in head, \
                    "publisher UPSERT 에 리드 관측 시각 가드가 붙었다(ALPHA-696 범위 밖)"
            elif "DO UPDATE" in head:
                # ⚠️ SET 에 시각이 빠져도 이 픽스처는 `observed` 를 늘 저장하므로 **적극적으로
                # 가린다**. 그 회귀는 운영에서 조용하다: 배치가 정당하게 이겨 리드를 쓰는데
                # 시각은 1분 경로의 옛 주장이 남아, 이후 그보다 이른 `fetched_at` 을 가진
                # 배치 런이 전부 차단된다 — **배치가 한 적 없는 주장이 배치 자신의 낡은
                # 리드를 보호**한다. 그래서 값이 아니라 문면으로 못박는다.
                assert ("SET LEAD_TEXT = EXCLUDED.LEAD_TEXT,"
                        " LEAD_OBSERVED_AT = EXCLUDED.LEAD_OBSERVED_AT") in head, \
                    "배치가 리드만 쓰고 관측 시각을 안 남긴다 — 회복 경로가 죽는다"
                where = head.split("ON CONFLICT", 1)[1].split("WHERE", 1)[1]
                # ⚠️ 부분문자열 존재만 보면 **결합자**(AND/OR·괄호)가 안 걸린다. `AND` 를
                # `OR` 로 바꾸거나 괄호만 지워도(그때는 AND 가 더 강하게 묶인다) 리드가 완전히
                # 같은데도 UPDATE 가 나가, 이 파일이 계약으로 못박은 "*_written 은 이번 런이
                # 값을 바꾼 건수"가 멱등 재실행에서 거짓이 된다. 절 전문을 그대로 본다.
                assert where.strip() == (
                    "NEWS_DOCUMENT.LEAD_TEXT IS DISTINCT FROM EXCLUDED.LEAD_TEXT"
                    " AND (NEWS_DOCUMENT.LEAD_OBSERVED_AT IS NULL"
                    " OR NEWS_DOCUMENT.LEAD_OBSERVED_AT <= EXCLUDED.LEAD_OBSERVED_AT)"
                ), f"배치 리드 가드의 WHERE 전문이 계약(ALPHA-696 ③)과 다르다: {where.strip()}"
            if " VALUES " in head:
                document_id, value = params
            elif column == "lead_text":
                value, observed, source_code, source_document_id = params
                document_id = self._conn.documents.get((source_code, source_document_id))
            else:
                value, source_code, source_document_id = params
                document_id = self._conn.documents.get((source_code, source_document_id))
            if not document_id:
                self.rowcount = 0            # 서브쿼리가 0행 — 넣을 대상 자체가 없다
                return
            # 행 존재를 모델링한다 — 일반 기사는 lead_text 문이 행을 먼저 만들어 publisher
            # 문이 **항상 충돌 경로**로 실행된다. DO UPDATE 가 DO NOTHING 으로 회귀하면
            # 실제 PG 는 충돌 행을 손대지 않는다 — 픽스처도 같은 결과를 내야 그 회귀가
            # 초록으로 숨지 않는다.
            if (document_id in self._conn.news_document_ids
                    and "DO UPDATE" not in head):
                self.rowcount = 0
                return
            store = (self._conn.publishers if column == "publisher"
                     else self._conn.lead_texts)
            rows = (self._conn.news_document_publishers if column == "publisher"
                    else self._conn.news_documents)
            # `ON CONFLICT (document_id) DO UPDATE ... WHERE <컬럼> IS DISTINCT FROM`:
            # 이미 같은 값이면 Postgres 는 **0행**을 돌려준다. 픽스처가 늘 1을 주면
            # *_written 이 멱등 재실행에서 부풀어도 초록으로 통과한다. 그 WHERE 가
            # **자기 컬럼을 대상으로** 실제로 SQL 에 있는지까지 본다 — 절을 지우거나
            # 다른 컬럼으로 복붙하면 같은 값도 UPDATE 돼 1행이 되고, unchanged 카운터
            # 테스트가 부풂으로 잡는다.
            guard = (f"WHERE NEWS_DOCUMENT.{column.upper()}"
                     f" IS DISTINCT FROM EXCLUDED.{column.upper()}")
            if store.get(document_id, _ABSENT) == value and guard in head:
                self.rowcount = 0
                return
            # 승자 판정(ALPHA-696) — 충돌 갈래에서만 걸린다. 저장된 관측이 더 새로우면
            # 이 배치는 진다. `observed` 가 None(=canonical fetched_at 결손)이면 비교는
            # SQL 에서 UNKNOWN 이라 `IS NULL` 절 하나만 남는다.
            # ⚠️ 비교는 **실제 시각**으로 한다(ALPHA-848). ISO 문자열 부등호는 오프셋이
            # 다르면 Postgres 와 갈린다 — `2026-07-15T05:00:00+00:00` vs
            # `2026-07-15T13:00:00+09:00`(=04:00Z)이면 PG 는 배치를 막는데 문자열
            # 비교는 통과시킨다. 이 파일 기본 `published_at` 이 `+09:00` 이라 다음
            # 케이스가 그대로 밟는다(`normalize_news._fetched_at` 이 같은 함정을 적어 뒀다).
            if column == "lead_text" and document_id in self._conn.news_document_ids:
                stored = self._conn.lead_observed_at.get(document_id)
                if stored is not None and (observed is None
                                           or _instant(stored) > _instant(observed)):
                    self.rowcount = 0
                    return
            self.rowcount = 1
            store[document_id] = value
            if column == "lead_text":
                self._conn.lead_observed_at[document_id] = observed
            rows.append((document_id, value))
            self._conn.news_document_ids.add(document_id)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, existing: list[tuple] | dict[tuple, str] | None = None,
                 lead_texts: dict[str, str | None] | None = None,
                 lead_observed_at: dict[str, object] | None = None,
                 fail_after: int | None = None):
        self.log: list = []
        self.fail_after, self.document_inserts = fail_after, 0
        self.news_documents: list[tuple[str, str]] = []
        # 자연키 → **실제 행의** document_id. dict 로 주면 계산값과 다른 기존 id 를 심는 것이고
        # (ALPHA-628 회귀), list 로 주면 id 값은 상관없이 존재만 보는 테스트다.
        self.documents: dict[tuple, str] = (
            dict(existing) if isinstance(existing, dict)
            else {k: f"doc_pre{i}" for i, k in enumerate(existing or [])})
        # document_id → 기존 news_document.lead_text. `assemble_events` 가 id 만 먼저 넣어둔
        # 행은 None 으로 심는다 — 그게 UPSERT 가 존재하는 이유다.
        self.lead_texts: dict[str, str | None] = dict(lead_texts or {})
        # document_id → 지금 저장된 리드를 관측한 시각(ALPHA-696). 1분 경로가 쓴 행은
        # 값이 있고, 아무도 주장하지 않은 자리는 None 이다 — 배치의 승자 판정이 이걸 본다.
        self.lead_observed_at: dict[str, object] = dict(lead_observed_at or {})
        # publisher 는 lead_text 와 같은 UPSERT 규칙의 별도 축(ALPHA-695).
        self.publishers: dict[str, str | None] = {}
        self.news_document_publishers: list[tuple[str, str]] = []
        # news_document 행의 존재 집합 — 충돌(ON CONFLICT) 경로 모델링의 근거.
        # lead_texts 로 미리 심은 행도 존재하는 행이다.
        self.news_document_ids: set[str] = set(self.lead_texts)

    def cursor(self):
        return _FakeCursor(self)


def _fake_connect(conn):
    from contextlib import contextmanager

    @contextmanager
    def _c(config):
        yield conn

    return _c


def _db() -> DbConfig:
    return DbConfig(password="x")


def _inserts(conn) -> list:
    return [p for sql, p in conn.log if sql.upper().startswith("INSERT INTO DOCUMENT ")]


def _news_doc_inserts(conn) -> list:
    """news_document 에 실제로 실린 (document_id, lead_text) — 자연키 해석 **후** 값이다."""
    return conn.news_documents


def test_new_article_becomes_a_news_document_row(tmp_path, monkeypatch):
    """document 는 assertion FK 의 뿌리다 — 자연키(source_code, source_document_id)와
    시간 축(published_at·available_at)이 canonical 그대로 실려야 다운스트림이 문서를 찾는다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "R1", db=_db()) == 0

    [(doc_id, source_code, source_document_id, title, language, published_at,
      available_at, source_uri)] = _inserts(conn)
    assert doc_id.startswith("doc_")  # ADR-0027 접두사+ULID
    assert (source_code, source_document_id) == ("bigkinds", "a1")
    assert language == "ko"
    assert published_at == "2026-07-15T09:00:00+09:00"
    assert available_at == "2026-07-15T01:00:00+00:00"  # fetched_at = 우리가 얻은 시각
    assert source_uri == "https://news.example/a1"
    assert title == "삼성전자 신규 수주"


def test_existing_article_is_not_recreated(tmp_path, monkeypatch):
    """멱등 — 재실행이 기존 행을 덮거나 새 document_id 로 갈아치우면 그 문서를 참조할
    assertion FK 가 전부 끊긴다(ALPHA-376). ON CONFLICT DO NOTHING(rowcount 0)이면 created
    가 아니라 already 로 세어져야 로그가 거짓말하지 않는다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn(existing=[("bigkinds", "a1")])
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "R1", db=_db()) == 0
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["created"] == 0
    assert log["already_present"] == 1
    assert log["created_rows_sample"] == []


def test_missing_identity_is_skipped_not_inserted(tmp_path, monkeypatch):
    """자연키(source_vendor·article_id) 결손 행을 넣으면 멱등의 근거가 사라져 같은 기사가
    매 런 새 행으로 쌓이거나 NOT NULL 위반으로 런 전체가 롤백된다 — 세고 뺀다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [
        _article("a1"),
        _article("", source_vendor="bigkinds"),      # article_id 결손
        _article("a3", source_vendor=None),           # source_vendor 결손
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "R1", db=_db()) == 0
    assert [p[2] for p in _inserts(conn)] == ["a1"], "자연키 결손 행이 적재됐다"


def test_available_at_falls_back_to_published_at_or_skips(tmp_path, monkeypatch):
    """available_at 은 NOT NULL — fetched_at 결손이면 published_at 이 대신하고, 둘 다 없으면
    시간 축이 없는 문서라 적재 불가(넣으면 즉시 제약 위반으로 런 전체 롤백)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [
        _article("a1", fetched_at=None),                          # → published_at 폴백
        _article("a2", fetched_at=None, published_at=None),       # → skip
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "R1", db=_db()) == 0
    [(_, _, article_id, _, _, _, available_at, _)] = _inserts(conn)
    assert article_id == "a1"
    assert available_at == "2026-07-15T09:00:00+09:00"
    # 결손이 로그에 세어져야 운영이 믿을 수 있다(Rule 12) — skip 은 침묵이 아니다.
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["skipped_no_available_at"] == 1


def test_window_prunes_partitions(tmp_path, monkeypatch):
    """--from/--to 는 published_date 파티션 프루닝이다 — 창 밖 파티션을 읽으면 백필/증분
    분리가 안 되고 스캔 비용이 창에 비례하지 않는다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-14", [_article("a-old")])
    _write_canonical(storage, "ko", "2026-07-15", [_article("a-in")])
    _write_canonical(storage, "ko", "2026-07-16", [_article("a-future")])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "R1", db=_db(),
                              from_date="2026-07-15", to_date="2026-07-15") == 0
    assert [p[2] for p in _inserts(conn)] == ["a-in"]


def test_same_article_in_two_partitions_is_created_once(tmp_path, monkeypatch):
    """같은 (source_vendor, article_id)가 두 파티션에 오면(같은 URL 재게시) 자연키가 하나라
    문서도 하나여야 한다 — 두 번 넣으면 uq_document_source 위반으로 런 전체가 롤백된다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-14", [_article("a1")])
    _write_canonical(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "R1", db=_db()) == 0
    assert len(_inserts(conn)) == 1


def test_both_languages_are_loaded(tmp_path, monkeypatch):
    """en 문서도 assertion 이 생기는 순간 FK 대상이다 — ko 만 실으면 영어 뉴스 assertion
    적재(후속)가 열릴 때 FK 뿌리가 없다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [_article("a-ko")])
    _write_canonical(storage, "en", "2026-07-15",
                     [_article("a-en", source_vendor="fmp", title="Samsung wins order")])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "R1", db=_db()) == 0
    by_article = {p[2]: p[4] for p in _inserts(conn)}  # article_id → language_code
    assert by_article == {"a-ko": "ko", "a-en": "en"}


def test_run_log_records_what_happened(tmp_path, monkeypatch):
    """조용한 0건 금지 — 몇 건 읽고 몇 건 걸렀고 몇 건 만들었는지가 남아야 한다(Rule 12)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [
        _article("a1"),
        _article("", source_vendor="bigkinds"),
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "R1", db=_db()) == 0
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["articles_read"] == 2
    assert log["skipped_missing_identity"] == 1
    assert log["created"] == 1
    assert log["created_rows_sample"][0]["source_document_id"] == "a1"


def test_db_failure_is_recorded_not_a_silent_traceback(tmp_path, monkeypatch):
    """DB 가 터지면 트레이스백으로 죽는 게 아니라 **비0 종료 + 로그**로 드러나야 한다.
    롤백된 런의 created 를 로그가 만들었다고 주장하면 다음 사람이 DB 에 있다고 믿는다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [_article("a1")])

    from contextlib import contextmanager

    @contextmanager
    def _boom(config):
        raise RuntimeError("DB 연결 끊김")
        yield  # pragma: no cover

    monkeypatch.setattr(load_documents, "connect", _boom)

    assert load_documents.run(storage, "R1", db=_db()) == 1, "실패가 성공으로 위장됐다"
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    assert len(keys) == 1, "실패했는데 로그가 안 남았다"
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["exit_code"] == 1
    assert log["failures"][0]["reasons"] == ["load_error"]
    assert log["created"] == 0, "롤백됐는데 만들었다고 로그가 주장한다"
    assert log["created_rows_sample"] == []


def test_document_id_is_derived_from_the_natural_key(tmp_path, monkeypatch):
    """적재되는 document_id 가 자연키에서 결정적으로 나온다(ALPHA-456).

    WHY: document_id 는 계보의 뿌리다 — assertion_id = f(document_id, …) 이고
    source_event_id = f(assertion_id, …) 이다. 이 한 줄이 랜덤이면 그 위 전부가 랜덤을
    상속해, assemble-events 가 선언한 "결정적 ID" 계약이 뿌리에서 무너진다.

    이 테이블도 writer 가 둘이라(이 스텝·assemble-events) 산식이 갈리면 ON CONFLICT
    DO NOTHING 때문에 먼저 쓴 쪽 값이 남는다. 두 스텝이 **같은 함수·같은 재료**를 쓰는지
    함께 고정한다.
    """
    from data_pipeline.db import stable_domain_id
    from data_pipeline.steps import assemble_events

    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "R1", db=_db()) == 0

    [(doc_id, *_rest)] = _inserts(conn)
    assert doc_id == stable_domain_id("doc", "bigkinds", "a1")
    # assemble-events 도 같은 재료로 같은 값을 낸다 — 공유가 끊기면 여기서 깨진다
    assert doc_id == assemble_events._stable_id("doc", "bigkinds", "a1")


def test_lead_text_is_loaded_into_news_document(tmp_path, monkeypatch):
    """BigKinds 스니펫이 news_document.lead_text 로 실려야 한다.

    WHY: canonical 은 이미 `CONTENT`→`lead_text` 를 갖고 있는데 여기서 안 실으면
    분석엔진 프롬프트가 **제목만** 보게 된다 — 사건의 내용(금액·상대·조건)이 설명에
    도달하지 못한다. 적재 여부가 곧 그 축의 활용 가능성이라 계약으로 고정한다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15",
                     [_article("a1", lead_text="삼성전자가 2734억원 규모 수주를 공시했다.")])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "run-1", db=_db(), from_date="2026-07-15",
                              to_date="2026-07-15") == 0

    [(document_id, lead_text)] = _news_doc_inserts(conn)
    assert lead_text == "삼성전자가 2734억원 규모 수주를 공시했다."
    assert document_id == _inserts(conn)[0][0]  # document 와 같은 결정적 id 로 붙는다


def test_publisher_is_loaded_into_news_document(tmp_path, monkeypatch):
    """canonical `publisher`(언론사)가 news_document.publisher 로 실려야 한다 (ALPHA-695).

    WHY: 정규화가 벤더별 필드를 표준행 `publisher` 로 살려 오는데 적재가 안 담으면
    원장의 출처 축이 수집 벤더(bigkinds) 하나로 접힌다 — 콘솔 문서 목록·언론사별
    유실 진단이 전부 불가능해진다. lead_text 없이 publisher 만 있어도 실려야 한다
    (품질 게이트가 둘을 각각 non-blocking 경고로 두는 것과 같은 독립성).
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15",
                     [_article("a1", lead_text=None, publisher="한국경제")])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "run-1", db=_db(), from_date="2026-07-15",
                              to_date="2026-07-15") == 0

    assert conn.news_documents == []            # lead_text 문은 나가지 않았다
    [(document_id, publisher)] = conn.news_document_publishers
    assert publisher == "한국경제"
    assert document_id == _inserts(conn)[0][0]  # document 와 같은 결정적 id 로 붙는다


def test_publisher_upsert_survives_the_row_lead_text_created_first(tmp_path, monkeypatch):
    """lead_text 문이 먼저 만든 news_document 행과 충돌해도 publisher 가 실린다 (ALPHA-695).

    WHY: 일반 기사는 둘 다 있어 publisher 문은 **항상 충돌 경로(DO UPDATE)** 로
    실행된다 — 이게 이 문의 주경로다. `ON CONFLICT DO NOTHING` 으로 회귀하면 실제
    PG 는 충돌 행을 손대지 않아 publisher 가 영영 안 채워진다. 픽스처가 행 존재를
    모델링하므로 그 회귀는 여기서 빨갛게 드러난다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15",
                     [_article("a1", lead_text="리드문", publisher="한국경제")])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "run-1", db=_db(), from_date="2026-07-15",
                              to_date="2026-07-15") == 0

    [(doc_id, lead_text)] = conn.news_documents
    [(pub_doc_id, publisher)] = conn.news_document_publishers
    assert (lead_text, publisher) == ("리드문", "한국경제")
    assert doc_id == pub_doc_id                  # 같은 행에 붙는다


def test_lead_text_is_filled_even_when_document_already_exists(tmp_path, monkeypatch):
    """document 가 이미 있어도(rowcount 0) 스니펫은 채운다.

    WHY: `assemble_events` 가 `news_document(document_id)` 만 먼저 넣어두는 경로가 있어,
    document 존재를 이유로 건너뛰면 이미 조립된 사건은 **영원히** 스니펫을 못 받는다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [_article("a1", lead_text="리드문")])
    # assemble_events 가 남긴 자국을 실제로 심는다 — document 행도, lead_text 없는
    # news_document 행도 이미 있는 상태다. 안 심으면 이 테스트는 UPSERT 경로를 안 밟는다.
    conn = _FakeConn(existing=[("bigkinds", "a1")], lead_texts={"doc_pre0": None})
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "run-1", db=_db(), from_date="2026-07-15",
                              to_date="2026-07-15") == 0

    assert [p[1] for p in _news_doc_inserts(conn)] == ["리드문"]


def test_lead_text_attaches_to_the_existing_row_id_not_the_computed_one(tmp_path, monkeypatch):
    """자연키가 **다른 document_id** 로 이미 있으면 news_document 는 그 id 로 붙는다.

    WHY: 2026-07-29 23:50 뉴스 런을 죽인 결함이다(ALPHA-628). document INSERT 가 DO NOTHING
    이라 기존 행의 id 가 남는데, ALPHA-456(결정적 ID) 이전에 적재된 6,674 행은 랜덤 ULID id 를
    갖고 있어 계산값과 갈린다. 계산값으로 news_document 를 넣으면 없는 문서를 참조해
    fk_news_document_type 이 터지고, 커밋 경계가 런 전체라 **하루치가 전량 롤백**된다.
    """
    from data_pipeline.db import stable_domain_id

    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [_article("a1", lead_text="리드문")])
    legacy_id = "doc_01KXPRDK0C5R7JJ1EFTWYT35ZC"   # dev 에 실재하는 ALPHA-456 이전 ULID
    assert legacy_id != stable_domain_id("doc", "bigkinds", "a1")   # 갈렸다는 전제를 못박는다
    conn = _FakeConn(existing={("bigkinds", "a1"): legacy_id})
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "run-1", db=_db()) == 0

    assert _news_doc_inserts(conn) == [(legacy_id, "리드문")]


def test_unchanged_lead_text_is_not_counted_as_written(tmp_path, monkeypatch):
    """이미 같은 값이면 DO UPDATE 의 WHERE 가 막아 0행 — 그걸 written 으로 세면 안 된다.

    WHY: `lead_text_written` 은 "이번 런이 실제로 채운 건수"다. 멱등 재실행마다 전건을
    다시 세면 그 수가 신선도 신호로서 무의미해지고, 0 이면 canonical 에 스니펫이 없다는
    뜻이라던 로그 계약도 거짓이 된다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [_article("a1", lead_text="리드문")])
    conn = _FakeConn(existing=[("bigkinds", "a1")], lead_texts={"doc_pre0": "리드문"})
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "run-1", db=_db()) == 0

    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["lead_text_written"] == 0
    assert _news_doc_inserts(conn) == []


def test_unchanged_publisher_is_not_counted_as_written(tmp_path, monkeypatch):
    """publisher 도 같은 값이면 0행 — lead_text 와 같은 unchanged 규칙 (ALPHA-695).

    WHY: `publisher_written` 이 멱등 재실행마다 전건으로 부풀면 "이번 런이 실제로
    채운 건수"라는 로그 계약이 거짓이 된다. 픽스처의 IS DISTINCT FROM guard 가
    **자기 컬럼**을 볼 때만 0행을 주므로, WHERE 절이 다른 컬럼으로 복붙되는 회귀도
    이 테스트의 부풂(1≠0)으로 드러난다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15",
                     [_article("a1", lead_text=None, publisher="한국경제")])
    conn = _FakeConn(existing=[("bigkinds", "a1")])
    conn.publishers["doc_pre0"] = "한국경제"
    conn.news_document_ids.add("doc_pre0")
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "run-1", db=_db()) == 0

    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["publisher_written"] == 0
    assert conn.news_document_publishers == []


def test_rollback_does_not_claim_lead_texts_it_never_kept(tmp_path, monkeypatch):
    """런 도중 터지면 앞서 성공한 UPSERT 도 롤백된다 — lead_text_written 도 0 이어야 한다.

    WHY: `created` 는 이미 0 으로 되돌리는데 `lead_text_written` 만 남으면, 스니펫이 DB 에
    실렸다고 믿고 분석엔진이 왜 제목만 보는지를 다시 찾게 된다. 계측이 틀릴 때 방향이
    **관대한 쪽**이면 아무도 결손을 못 본다(Rule 12).
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15",
                     [_article("a1", lead_text="리드문", fetched_at=None),
                      _article("a2", lead_text="리드문2")])
    conn = _FakeConn(fail_after=1)   # a1 은 통과, a2 의 document INSERT 에서 터진다
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "R1", db=_db()) == 1
    assert _news_doc_inserts(conn) == [(conn.documents[("bigkinds", "a1")], "리드문")]

    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["created"] == 0
    assert log["lead_text_written"] == 0, "롤백됐는데 스니펫을 채웠다고 로그가 주장한다"
    # 반대 방향도 고정한다 — `lead_unclaimed_freshness` 는 **쓰기 수가 아니라 관측 노출
    # 수**라 롤백에서 지우면 안 된다(ALPHA-696). 지우면 canonical `fetched_at` 이 결손이라
    # 축이 무력화되는 중인 벤더가 하필 DB 장애로 죽은 런에서 0 을 보고해, 진단이 가장
    # 필요한 순간에 근거가 사라진다. 위 세 줄과 같이 두는 이유가 그 대비다.
    assert log["lead_unclaimed_freshness"] == 1, \
        "롤백이 관측 노출 수까지 지웠다 — 축이 무력화된 것을 볼 자리가 없어진다"


def test_missing_lead_text_writes_no_news_document_row(tmp_path, monkeypatch):
    """스니펫이 없으면 행을 만들지 않는다 — 빈 lead_text 로 덮어 기존 값을 지우지 않도록."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [_article("a1", lead_text=None)])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "run-1", db=_db(), from_date="2026-07-15",
                              to_date="2026-07-15") == 0

    assert _news_doc_inserts(conn) == []


# ── 두 생산자 승자 규칙 (ALPHA-696) ──────────────────────────────────────────
#
# 이 표에는 생산자가 둘이다 — 이 배치와 1분 `PgNewsCanonicalWriter`. 아래 넷은 전부
# **결과가 그럴듯해 보이는 회귀**라, 규칙이 SQL 에서 빠져도 다른 테스트는 초록이다.

def test_batch_does_not_revert_a_fresher_minute_correction(tmp_path, monkeypatch):
    """1분 경로가 반영한 정정을 레이크의 옛 값으로 되돌리면 안 된다.

    WHY: 되돌리면 원장은 새 지문(fp2)을 확정했는데 Consumer 는 옛 본문을 읽는다 —
    ALPHA-691 이 고치려던 P1 그대로고, 읽은 본문이 그 지문의 것인지 확인할 수단이
    없어 **아무도 탐지하지 못한다**. 이 배치가 이기는 유일한 근거는 자기 관측이
    더 새롭다는 것뿐이다.
    """
    from data_pipeline.db import stable_domain_id

    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15",
                     [_article("a1", lead_text="옛 본문 T1",
                               fetched_at="2026-07-15T01:00:00+00:00")])
    doc_id = stable_domain_id("doc", "bigkinds", "a1")
    conn = _FakeConn(existing={("bigkinds", "a1"): doc_id},
                     lead_texts={doc_id: "정정된 본문 T2"},
                     lead_observed_at={doc_id: "2026-07-15T04:00:00+00:00"})
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "run-1", db=_db(), from_date="2026-07-15",
                              to_date="2026-07-15") == 0

    assert conn.lead_texts[doc_id] == "정정된 본문 T2"
    assert conn.news_documents == []            # 쓰기 자체가 안 나갔다


def test_batch_wins_once_its_own_collection_is_newer(tmp_path, monkeypatch):
    """반대쪽 — 일일 수집이 그 기사를 다시 담으면 배치가 다시 이겨야 한다.

    WHY: 정상 회복 경로다. "1분 경로가 무조건 이긴다"로 짜면 1분 레인이 안 도는
    벤더·과거 백필에서 리드가 영영 고정되고, 정규화 규칙이 고쳐져도 반영할 길이 없다.
    """
    from data_pipeline.db import stable_domain_id

    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15",
                     [_article("a1", lead_text="재수집된 본문 T3",
                               fetched_at="2026-07-15T06:00:00+00:00")])
    doc_id = stable_domain_id("doc", "bigkinds", "a1")
    conn = _FakeConn(existing={("bigkinds", "a1"): doc_id},
                     lead_texts={doc_id: "정정된 본문 T2"},
                     lead_observed_at={doc_id: "2026-07-15T04:00:00+00:00"})
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "run-1", db=_db(), from_date="2026-07-15",
                              to_date="2026-07-15") == 0

    assert conn.lead_texts[doc_id] == "재수집된 본문 T3"


def test_batch_still_fills_a_seat_nobody_claimed(tmp_path, monkeypatch):
    """아무도 관측을 주장하지 않은 빈 자리는 그대로 채운다.

    WHY: `assemble_events` 는 `document_id` 만으로 행을 먼저 만든다. 그 자리를 시각
    가드가 막으면 ALPHA-628 이 되찾아 온 리드 승격이 통째로 죽는다 — P1 하나를
    닫으면서 유실 경로를 새로 여는 것이다.
    """
    from data_pipeline.db import stable_domain_id

    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15",
                     [_article("a1", lead_text="승격되어야 할 스니펫")])
    doc_id = stable_domain_id("doc", "bigkinds", "a1")
    conn = _FakeConn(existing={("bigkinds", "a1"): doc_id},
                     lead_texts={doc_id: None})     # 시각은 심지 않는다 = 미주장
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "run-1", db=_db(), from_date="2026-07-15",
                              to_date="2026-07-15") == 0

    assert conn.lead_texts[doc_id] == "승격되어야 할 스니펫"


def test_batch_without_a_collection_time_claims_no_freshness(tmp_path, monkeypatch):
    """`fetched_at` 이 없으면 신선도를 주장하지 못한다 — 그리고 그 사실이 세어진다.

    WHY: `available_at` 은 `fetched_at or published_at` 이라 결손 시 **미래 발행일**이
    들어온다. 그 값을 축으로 쓰면 미래 시각이 박혀 이 행의 리드 승격이 영구 차단된다.
    그래서 결손이면 미주장으로 두는데, 그러면 그 벤더에서 축이 조용히 무력화되므로
    노출 건수를 로그에 남긴다(Rule 12 — 안 세면 볼 계기가 없다).
    """
    from data_pipeline.db import stable_domain_id

    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15",
                     [_article("a1", lead_text="레이크 스니펫", fetched_at=None)])
    doc_id = stable_domain_id("doc", "bigkinds", "a1")
    conn = _FakeConn(existing={("bigkinds", "a1"): doc_id},
                     lead_texts={doc_id: "1분 경로가 쓴 본문"},
                     lead_observed_at={doc_id: "2026-07-15T04:00:00+00:00"})
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "run-1", db=_db(), from_date="2026-07-15",
                              to_date="2026-07-15") == 0

    assert conn.lead_texts[doc_id] == "1분 경로가 쓴 본문"   # 주장 못 했으니 진다
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["lead_unclaimed_freshness"] == 1


# ── 빈 문자열 구멍 + 관측 분모 (ALPHA-848) ──────────────────────────────────

def test_empty_fetched_at_does_not_kill_the_whole_run(tmp_path, monkeypatch):
    """canonical `fetched_at` 이 `""` 여도 런이 죽지 않고 그 행만 미주장으로 처리된다.

    WHY: 커밋 경계가 런 전체라, 셀 하나의 빈 문자열이 timestamptz 파싱 에러를 내면
    **그날 문서 적재가 통째로 롤백**된다. 가드(`if not fetched_at`)는 falsy 를 '값
    없음'으로 읽는데 바인딩만 '값'으로 읽던 불일치였다 — 두 판정이 같은 값을 봐야 한다.
    `_text()` 는 str 여부만 보증하고 `_fetched_at()` 은 정렬 키라 빈 값을 안 거른다.
    """
    from data_pipeline.db import stable_domain_id

    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15",
                     [_article("a1", lead_text="리드문", fetched_at="",
                               published_at="2026-07-15T09:00:00+09:00")])
    doc_id = stable_domain_id("doc", "bigkinds", "a1")
    conn = _FakeConn(existing={("bigkinds", "a1"): doc_id})
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "run-1", db=_db(), from_date="2026-07-15",
                              to_date="2026-07-15") == 0

    # 리드는 들어갔고, 시각은 **주장하지 않았다**(NULL) — 빈 문자열이 아니다.
    assert conn.lead_texts[doc_id] == "리드문"
    assert conn.lead_observed_at[doc_id] is None
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["lead_unclaimed_freshness"] == 1
    assert log["exit_code"] == 0


def test_lead_attempted_is_the_denominator_of_the_unclaimed_counter(tmp_path, monkeypatch):
    """`lead_unclaimed_freshness` 옆에 시도 수가 같이 남는다.

    WHY: 분자만 있으면 `137` 이 137/140(그 벤더에서 승자 축이 죽었다)인지 137/60,000
    (잡음)인지 못 가른다 — 이 카운터의 존재 이유가 바로 그 판단이다. 리드가 없어 UPSERT
    자체를 안 한 기사는 분모에 안 들어간다(그건 소스 결손이지 축 문제가 아니다).
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [
        _article("a1", lead_text="리드1", fetched_at="2026-07-15T01:00:00+00:00"),
        _article("a2", lead_text="리드2", fetched_at=""),          # 주장 못 함
        _article("a3", lead_text=None, fetched_at=""),             # 리드 없음 → 분모 밖
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "run-1", db=_db(), from_date="2026-07-15",
                              to_date="2026-07-15") == 0

    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["lead_attempted"] == 2
    assert log["lead_unclaimed_freshness"] == 1


def test_offset_only_difference_still_blocks_the_batch(tmp_path, monkeypatch):
    """픽스처가 시각을 **절대 시각**으로 비교한다 — 오프셋만 다른 쌍에서 PG 와 갈리면 안 된다.

    WHY: `2026-07-15T13:00:00+09:00` 은 `2026-07-15T05:00:00+00:00` 보다 **이르다**(=04:00Z).
    ISO 문자열 부등호는 `"2026-07-15T13..." > "2026-07-15T05..."` 라 반대로 답해, 실 PG 가
    막는 되돌림을 픽스처만 통과시킨다. 이 파일 기본 `published_at` 이 `+09:00` 이라
    다음 케이스가 그대로 밟는 함정이다.
    """
    from data_pipeline.db import stable_domain_id

    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15",
                     [_article("a1", lead_text="옛 본문 T1",
                               fetched_at="2026-07-15T13:00:00+09:00")])   # = 04:00Z
    doc_id = stable_domain_id("doc", "bigkinds", "a1")
    conn = _FakeConn(existing={("bigkinds", "a1"): doc_id},
                     lead_texts={doc_id: "정정된 본문 T2"},
                     lead_observed_at={doc_id: "2026-07-15T05:00:00+00:00"})  # 더 새롭다
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "run-1", db=_db(), from_date="2026-07-15",
                              to_date="2026-07-15") == 0

    assert conn.lead_texts[doc_id] == "정정된 본문 T2"   # 문자열 비교였다면 되돌아간다
