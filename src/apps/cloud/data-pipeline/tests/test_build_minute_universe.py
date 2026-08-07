"""build_minute_universe — 섹터 후보 ETF 합집합.

각 테스트는 '왜 이 동작이 중요한가'를 주석으로 남긴다 — AGENTS Rule 9.
"""

import importlib.util
import io
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data_pipeline.lake import LocalStorage, canonical_etf_holdings_partition

_SPEC = importlib.util.spec_from_file_location(
    "build_minute_universe",
    Path(__file__).resolve().parents[1] / "scripts" / "build_minute_universe.py",
)
build_minute_universe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_minute_universe)
build = build_minute_universe.build


def _holdings(storage, as_of: str, rows: list[tuple[str, str]]) -> None:
    """canonical KR holdings 스냅샷 — (constituent_ticker, etf_id) 쌍."""
    schema = pa.schema([("etf_id", pa.string()), ("constituent_ticker", pa.string())])
    table = pa.Table.from_pylist(
        [{"etf_id": e, "constituent_ticker": c} for c, e in rows], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(
        f"{canonical_etf_holdings_partition('KR', as_of)}/part-00000.parquet", buf.getvalue())


def _storage(tmp_path) -> LocalStorage:
    storage = LocalStorage(tmp_path / "lake")
    _holdings(storage, "2026-08-07", [("005930", "091160"), ("000660", "091160")])
    return storage


def test_sector_etfs_join_the_universe(tmp_path):
    # WHY: 구간(장중) 모드의 섹터층은 섹터 ETF 로 선다 — 일봉 경로가 쓰는 KRX 업종지수는
    #      분봉이 없기 때문이다(수집 원천이 pykrx 일봉). 그 후보가 유니버스에 없으면 계열이
    #      아예 없어 섹터층이 통째로 빠지고, 남은 시장·고유가 섹터 몫까지 떠안는다.
    universe = build(_storage(tmp_path), frozenset({"091160"}), ("091170", "102970"))

    assert set(universe.etf_ids) == {"091160", "091170", "102970"}
    # 섹터 ETF 는 **자기 분봉만** 필요하다 — 구성종목은 한 종도 안 늘어난다.
    assert set(universe.constituent_ids) == {"005930", "000660"}


def test_sector_etf_that_is_also_a_constituent_lands_on_the_etf_axis(tmp_path):
    # WHY: `Universe` 는 etf_ids 와 constituent_ids 가 겹치면 거부한다. 섹터 ETF 가 다른
    #      ETF 의 보유 종목으로 잡혀 있으면(ETF-of-ETF·현금성 편입) 두 축에 동시에 들어가
    #      universe 생성이 통째로 죽는다 — ETF 축이 이겨야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _holdings(storage, "2026-08-07", [("005930", "091160"), ("091170", "091160")])

    universe = build(storage, frozenset({"091160"}), ("091170",))

    assert set(universe.etf_ids) == {"091160", "091170"}
    assert set(universe.constituent_ids) == {"005930"}


def test_universe_version_moves_when_sector_set_changes(tmp_path):
    # WHY: universe_version 은 세션 identity 축이다(worker·consumer 가 원장 값과 대조해
    #      갈리면 처리를 거부한다). 섹터 후보를 더했는데 version 이 그대로면 새 집합이
    #      옛 세션에 그대로 붙어, 기대 유니버스와 실제 수집 집합이 조용히 갈린다.
    storage = _storage(tmp_path)

    before = build(storage, frozenset({"091160"}), ())
    after = build(storage, frozenset({"091160"}), ("091170",))

    assert before.universe_version != after.universe_version


def test_empty_sector_list_keeps_the_old_universe(tmp_path):
    # WHY: 이 설정은 선택 항목이다(섹션 미설정 = 빈 튜플). 빈 목록이 version 을 흔들면
    #      배포만으로 hash 가 바뀌어 그날 재계획이 UniverseConflictError 로 막힌다.
    storage = _storage(tmp_path)

    assert build(storage, frozenset({"091160"}), ()).universe_version == (
        build(storage, frozenset({"091160"})).universe_version)


def test_no_holdings_still_fails_loud(tmp_path):
    # WHY: 섹터 목록만으로 ETF 축이 차면 "holdings 를 못 읽었다"가 가려진다 — 구성종목 0 인
    #      유니버스가 만들어져 그날 1분 수집이 ETF 몇 종으로 쪼그라든 채 초록으로 돈다.
    storage = LocalStorage(tmp_path / "lake")

    with pytest.raises(SystemExit, match="유니버스를 못 만들었다"):
        build(storage, frozenset({"091160"}), ("091170", "102970"))
