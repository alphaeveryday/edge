"""build_minute_universe — 참조 계열(섹터 후보) ETF 축 + 정본 객체 반영.

각 테스트는 '왜 이 동작이 중요한가'를 주석으로 남긴다 — AGENTS Rule 9.
"""

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data_pipeline.lake import LocalStorage, canonical_etf_holdings_partition
from data_pipeline.steps import build_minute_universe

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


def test_sector_etfs_are_collected_but_not_judged(tmp_path):
    # WHY: 구간(장중) 모드의 섹터층은 섹터 ETF 로 선다 — 일봉 경로가 쓰는 KRX 업종지수는
    #      분봉이 없기 때문이다. 그래서 봉은 받아야 한다(`unit_ids`). 그런데 `etf_ids` 는
    #      `price_consumer` 의 **판정 집합**이라(PriceTriggerHandler.etf_ids) 거기 얹으면
    #      이 계열이 트리거 발화 대상이 된다 — 레버리지·인버스가 3% 게이트를 상시 넘겨
    #      구성종목 없는 상품의 설명이 발화되고, 일봉 유니버스 밖이라 prev_close 가 없어
    #      `needs_open` 이 상시 비지 않는다. 수집 축과 판정 축을 갈라야 한다.
    universe = build(_storage(tmp_path), frozenset({"091160"}), ("091170", "102970"))

    assert set(universe.sector_etf_ids) == {"091170", "102970"}
    assert set(universe.etf_ids) == {"091160"}          # 판정 축은 안 늘어난다
    assert {"091170", "102970"} <= set(universe.unit_ids)  # 수집은 된다
    # 참조 계열은 **자기 분봉만** 받는다 — 구성종목은 한 종도 안 늘어난다.
    assert set(universe.constituent_ids) == {"005930", "000660"}


def test_sector_etf_that_is_also_a_constituent_lands_on_the_sector_axis(tmp_path):
    # WHY: `Universe` 는 세 축이 겹치면 거부한다. 섹터 ETF 가 다른 ETF 의 보유 종목으로
    #      잡혀 있으면(ETF-of-ETF·현금성 편입) 두 축에 동시에 들어가 universe 생성이
    #      통째로 죽는다 — 참조 계열 축이 이겨야 하고, 판정 축으로 새면 안 된다.
    storage = LocalStorage(tmp_path / "lake")
    _holdings(storage, "2026-08-07", [("005930", "091160"), ("091170", "091160")])

    universe = build(storage, frozenset({"091160"}), ("091170",))

    assert set(universe.sector_etf_ids) == {"091170"}
    assert set(universe.etf_ids) == {"091160"}
    assert set(universe.constituent_ids) == {"005930"}


def test_etf_declared_on_both_axes_fails_loud(tmp_path):
    # WHY: 두 설정이 같은 코드를 다르게 말하는 상태다 — etf_map 은 "판정해라",
    #      sector_etf_ids 는 "봉만 받아라". 어느 쪽으로든 조용히 넘기면 나머지 선언이
    #      거짓이 된다(참조 계열로 넘기면 트리거 대상이 소리 없이 사라지고, 판정 축으로
    #      넘기면 레버리지·인버스가 3% 게이트에 실린다). 평균내지 않고 사람에게 넘긴다.
    with pytest.raises(SystemExit, match="양쪽에 있다"):
        build(_storage(tmp_path), frozenset({"091160"}), ("091160",))


def test_both_axes_conflict_is_caught_from_config_not_just_holdings(tmp_path):
    # WHY: 겹침 대조군을 holdings 파생 집합으로만 잡으면, 그 ETF 의 스냅샷이 아직 없을
    #      때(신규 편입·KRX 런 실패·소급 상한 초과) 모순이 조용히 통과한다 — 그리고
    #      정작 그때가 사람이 목록을 손대는 시점이다. 정본인 etf_map 축으로도 본다.
    storage = LocalStorage(tmp_path / "lake")
    _holdings(storage, "2026-08-07", [("005930", "091160")])  # 091170 스냅샷은 아직 없다

    with pytest.raises(SystemExit, match="양쪽에 있다"):
        build(storage, frozenset({"091160", "091170"}), ("091170",))


def test_declared_etf_without_holdings_is_refused_not_silently_dropped(tmp_path):
    # WHY: `etf_ids` 는 config 가 아니라 holdings 파생이라(`_kr_etf_ids`), etf_map 이
    #      선언만 하고 스냅샷이 아직 없는 ETF 는 세 축 어디에도 안 실린다 — universe 에서
    #      통째로 사라지는데 나머지 종목이 멀쩡해 `not etf_ids` 게이트는 통과한다.
    #      이 창은 **참조 계열 → 판정 축 이동 때 반드시 열리고**(ALPHA-927), 그 사이
    #      만든 객체를 올리면 그 ETF 는 봉조차 못 받아 **옮기기 전보다 나빠진다**.
    #      그러니 조용한 누락이 아니라 거부여야 한다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _holdings(storage, "2026-08-07", [("005930", "091160")])  # 091170 스냅샷이 아직 없다

    with pytest.raises(SystemExit, match="091170"):
        build(storage, frozenset({"091160", "091170"}), ())


def test_declared_etf_with_holdings_still_builds(tmp_path):
    # WHY: 위 가드가 **결손일 때만** 물어야 한다. 선언 = 판정집합이 되는 정상 경로까지
    #      막으면 ETF 를 새로 넣는 일 자체가 불가능해진다 — 가드가 넓으면 우회당한다.
    storage = LocalStorage(tmp_path / "lake")
    _holdings(storage, "2026-08-07",
              [("005930", "091160"), ("105560", "091170")])

    universe = build(storage, frozenset({"091160", "091170"}), ())

    assert set(universe.etf_ids) == {"091160", "091170"}
    assert "105560" in universe.constituent_ids


def test_universe_version_moves_when_sector_set_changes(tmp_path):
    # WHY: universe_version 은 세션 identity 축이다(worker·consumer 가 원장 값과 대조해
    #      갈리면 처리를 거부한다). 참조 계열을 더했는데 version 이 그대로면 새 집합이
    #      옛 세션에 그대로 붙어, 기대 유니버스와 실제 수집 집합이 조용히 갈린다.
    storage = _storage(tmp_path)

    before = build(storage, frozenset({"091160"}), ())
    after = build(storage, frozenset({"091160"}), ("091170",))
    other = build(storage, frozenset({"091160"}), ("102970",))

    assert before.universe_version != after.universe_version
    assert after.universe_version != other.universe_version


def test_empty_sector_list_reproduces_the_pre_axis_version(tmp_path):
    # WHY: 이 축이 생기기 전에 착지한 universe 와 **같은 값**이 나와야 한다. 빈 축을
    #      무조건 checksum 에 넣으면 구성이 똑같은 기존 session 이 배포만으로 다른
    #      version 을 얻어 그날 재계획이 UniverseConflictError 로 막힌다.
    #      두 build() 를 비교하면 둘 다 같이 흔들려 못 잡는다 — 값을 못박는다.
    universe = build(_storage(tmp_path), frozenset({"091160"}), ())

    # 축 도입 전 규칙(`content_checksum([etf_ids, constituent_ids])`)이 내던 값
    assert universe.universe_version == "kr-holdings-1be3dcf0ba65"
    assert universe.sector_etf_ids == ()
    # hash 도 같은 규율 — 빈 축은 identity 에 안 들어간다. 여기서 두 build() 를
    # 비교하면 **항등식**이다(세 번째 인자의 기본값이 곧 빈 튜플이라 같은 호출이다)
    # — 어떤 구현이어도 통과한다. version 과 마찬가지로 값을 못박는다.
    assert universe.universe_hash == (
        "04e0e7ba29b4f21b86c50b1dbc359ac2e3809868b0223a3995896a5bf07541cb")


def test_no_holdings_still_fails_loud(tmp_path):
    # WHY: 참조 계열만으로 축이 차면 "holdings 를 못 읽었다"가 가려진다 — 구성종목 0 인
    #      유니버스가 만들어져 그날 1분 수집이 몇 종으로 쪼그라든 채 초록으로 돈다.
    #
    # 입력은 **선언 ETF 의 스냅샷은 있고 그 구성종목이 참조 계열뿐인** 형태다. 빈 레이크로
    # 두면 ALPHA-927 가드(선언했는데 holdings 에 없다)가 먼저 잡아 이 게이트에 안 닿는다 —
    # 그건 더 구체적인 진단이라 순서가 맞지만, 그러면 이 테스트가 **자기 경로를 안 밟는다**.
    storage = LocalStorage(tmp_path / "lake")
    _holdings(storage, "2026-08-07",
              [("091170", "091160"), ("102970", "091160")])

    with pytest.raises(SystemExit, match="유니버스를 못 만들었다"):
        build(storage, frozenset({"091160"}), ("091170", "102970"))


def test_settings_wiring_reaches_the_universe(tmp_path):
    # WHY: build() 만 직접 부르는 테스트는 **기능이 통째로 무력화된 채 초록으로 도는**
    #      경우를 못 잡는다 — main() 이 설정에서 목록을 안 꺼내면 섹터 후보 47종이
    #      universe.json 에서 조용히 사라지고 어느 테스트도 실패하지 않는다.
    settings = SimpleNamespace(
        krx_etf=SimpleNamespace(source=SimpleNamespace(etf_map={"091160": "KR7091160002"})),
        minute_universe=SimpleNamespace(sector_etf_ids=("091170", "102970")),
    )

    universe = build_minute_universe.build_from_settings(settings, _storage(tmp_path))

    assert set(universe.sector_etf_ids) == {"091170", "102970"}


def test_settings_wiring_without_the_section(tmp_path):
    # WHY: 섹션은 선택 항목이다. 미설정을 못 견디면 이 축이 없는 환경의 빌드가 통째로 죽는다.
    settings = SimpleNamespace(
        krx_etf=SimpleNamespace(source=SimpleNamespace(etf_map={"091160": "KR7091160002"})),
        minute_universe=None,
    )

    assert build_minute_universe.build_from_settings(
        settings, _storage(tmp_path)).sector_etf_ids == ()


def test_serialized_payload_carries_every_declared_axis(tmp_path):
    # WHY: 산출물이 손으로 나열한 필드 목록이면 축이 하나 늘 때 조용히 빠진다 — 그러면
    #      universe.json 에 참조 계열이 없어 수집이 예전 집합 그대로 돈다. 그리고 선언
    #      없는 축은 키 자체가 없어야 한다(빈 축을 생략하는 hash 규율과 같은 축).
    #      **스크립트가 쓰는 그 함수**를 부른다 — model_dump 를 직접 부르면 스크립트가
    #      손 나열로 되돌아가도 이 테스트는 초록이다.
    universe = build(_storage(tmp_path), frozenset({"091160"}), ("091170",))

    payload = json.loads(build_minute_universe.payload_of(universe))

    assert payload["sector_etf_ids"] == ["091170"]
    assert "extended_hours_ids" not in payload
    # 이 payload 로 다시 세운 universe 는 identity 가 같아야 한다(왕복 무손실)
    from data_pipeline.minute.models import Universe

    assert Universe.model_validate(payload).universe_hash == universe.universe_hash


def test_constituents_drained_by_the_sector_axis_fails_loud(tmp_path):
    # WHY: `not constituent_ids` 쪽 절반은 빈 레이크 테스트로는 안 밟힌다. 이 경로가
    #      죽으면 pydantic 의 `too_short` 가 대신 나오는데, 그건 "holdings 를 확인하라"는
    #      처방을 못 준다 — 무엇을 고칠지 모르는 실패는 fail-loud 가 아니다.
    storage = LocalStorage(tmp_path / "lake")
    _holdings(storage, "2026-08-07", [("091170", "091160")])  # 구성종목이 그 하나뿐

    with pytest.raises(SystemExit, match="구성종목 0종"):
        build(storage, frozenset({"091160"}), ("091170",))


# ── 정본 객체 반영 (ALPHA-953) ─────────────────────────────
RUN_ID = "20260812T070000Z"


class _CountingStorage(LocalStorage):
    """PUT 을 세는 레이크. no-op 계약은 **쓰기가 없었다**는 진술이라 결과 바이트로는
    판정할 수 없다 — 같은 payload 를 매번 PUT 해도 최종 상태가 같기 때문이다."""

    puts = 0

    def put_bytes(self, key: str, data: bytes) -> None:
        self.puts += 1
        super().put_bytes(key, data)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        krx_etf=SimpleNamespace(source=SimpleNamespace(etf_map={"091160": "KR7091160002"})),
        minute_universe=None,
    )


def _wide(storage, as_of: str, count: int) -> None:
    """구성종목 `count` 종을 가진 091160 스냅샷. 축소 판정은 unit 수로 갈리므로
    표본이 임계(10%)를 넘게 움직일 만큼은 있어야 한다."""
    _holdings(storage, as_of, [(f"{100000 + i:06d}", "091160") for i in range(count)])


def _lane(tmp_path, count: int = 20, as_of: str = "2026-08-07"):
    """(레이크, 정본 객체 경로). 객체는 레이크 **밖**에 둔다 — 운영에선 소비자가 받는
    `--universe` URI 이고, 레이크 키 규약과 다른 축이다."""
    storage = LocalStorage(tmp_path / "lake")
    _wide(storage, as_of, count)
    return storage, str(tmp_path / "config" / "universe.json")


def _bak(uri: str, run_id: str = RUN_ID) -> Path:
    return Path(f"{uri}.bak-{run_id}")


def test_unchanged_universe_is_not_written(tmp_path):
    # WHY: universe_version 은 구성에서 유도되므로 같은 구성이면 같은 객체다. 그런데도
    #      매일 PUT 하면 정본 객체의 세대가 매일 바뀌어, 나중에 "언제 유니버스가 실제로
    #      움직였나"를 객체 이력으로 못 되짚는다 — 그 이력이 사고 때 유일한 단서다.
    #      **쓰기 횟수로 단언한다** — 결과 바이트를 보면 매번 PUT 하는 회귀가 통과한다.
    storage, uri = _lane(tmp_path)
    assert build_minute_universe.run(storage, _settings(), uri, RUN_ID) == 0
    first = Path(uri).read_bytes()
    stat = Path(uri).stat()

    assert build_minute_universe.run(storage, _settings(), uri, "20260813T070000Z") == 0

    assert Path(uri).read_bytes() == first
    assert Path(uri).stat().st_mtime_ns == stat.st_mtime_ns   # 파일을 아예 안 열었다
    assert not list(Path(uri).parent.glob("*.bak-*"))


def test_new_constituent_lands_with_the_previous_object_backed_up(tmp_path):
    # WHY: 이 스텝의 존재 이유다 — 장전에 편입된 종목이 **그날** 분봉을 받으려면 세션
    #      계획 전에 정본이 바뀌어야 한다. 동시에 잘못 만든 유니버스가 그날의 유일한
    #      정본이 되면 되돌릴 것이 없으므로, 교체는 백업과 한 쌍이다.
    storage, uri = _lane(tmp_path)
    build_minute_universe.run(storage, _settings(), uri, "20260811T070000Z")
    before = Path(uri).read_bytes()
    _wide(storage, "2026-08-12", 21)  # 신규 편입 1종

    assert build_minute_universe.run(storage, _settings(), uri, RUN_ID) == 0

    after = json.loads(Path(uri).read_text(encoding="utf-8"))
    assert "100020" in after["constituent_ids"]
    assert _bak(uri).read_bytes() == before   # 직전 정본이 그대로 남는다


def test_shrunk_universe_is_refused_and_leaves_the_object_alone(tmp_path):
    # WHY: 줄어든 유니버스는 **초록으로** 돈다 — 빠진 종목은 window 기대 집합에서도
    #      빠져 결손으로 잡히지 않는다. 그리고 그날 분봉은 다시 오지 않아 복구가 없다
    #      (ALPHA-936 실증). 홀딩스 수집이 반쯤 실패한 아침에 조용히 착지하는 것이
    #      이 형태라, 거부는 물론 **기존 객체를 건드리지 않는 것**까지가 계약이다.
    storage, uri = _lane(tmp_path)
    build_minute_universe.run(storage, _settings(), uri, "20260811T070000Z")
    before = Path(uri).read_bytes()
    _wide(storage, "2026-08-12", 2)  # 수집 사고 — 스냅샷이 2종만 실렸다

    with pytest.raises(SystemExit, match="크게 줄어 거부"):
        build_minute_universe.run(storage, _settings(), uri, RUN_ID)

    assert Path(uri).read_bytes() == before
    assert not _bak(uri).exists()


def test_small_shrink_still_lands(tmp_path):
    # WHY: 가드가 넓으면 정상 리밸런싱(ETF 하나가 몇 종 갈아끼우는 일)까지 막아, 탈출구가
    #      "객체를 지운다"뿐인 상태로 매일 아침 사람을 부른다 — 그런 가드는 곧 꺼진다.
    storage, uri = _lane(tmp_path)
    build_minute_universe.run(storage, _settings(), uri, "20260811T070000Z")
    _wide(storage, "2026-08-12", 19)  # 21 unit → 20 unit (ETF 1 + 구성종목)

    assert build_minute_universe.run(storage, _settings(), uri, RUN_ID) == 0

    after = json.loads(Path(uri).read_text(encoding="utf-8"))
    assert len(after["constituent_ids"]) == 19


def test_the_step_writes_where_the_consumers_read(tmp_path):
    # WHY: 대상 객체를 상수로 박으면 terraform `var.minute_universe_uri` 가 기본값에서
    #      옮겨졌을 때 생산자와 소비자가 **둘 다 exit 0 으로** 갈린다 — 소비자는 옛
    #      정본을 계속 읽고, 아무 로그에도 안 드러난다. 그래서 쓸 자리는 인자다.
    #      `load_universe_uri`(소비자가 읽는 그 함수)로 되읽어 왕복을 확인한다.
    from data_pipeline.minute.models import load_universe_uri

    storage, uri = _lane(tmp_path)
    build_minute_universe.run(storage, _settings(), uri, RUN_ID)

    assert load_universe_uri(uri).etf_ids == ("091160",)


def test_missing_etf_map_root_is_refused_not_silently_widened(tmp_path):
    # WHY: `_krx_expected_etfs` 의 None 은 "0종"이 아니라 **뿌리 부재**다. 그대로 넘기면
    #      holdings 헬퍼가 "필터하지 않는다"로 읽어 레이크에 남은 폐지 ETF·옛 스냅샷까지
    #      실린 유니버스가 나오는데, 세 가드가 전부 `expected_etfs or ()` 라 빈 집합 대조가
    #      되어 통과한다 — 축이 조용히 넓어진 채 exit 0 이다. 파일만 뽑던 시절엔 사람이
    #      요약 줄에서 걸렀지만 이제 그 산출이 정본으로 반영되므로 여기서 막는다.
    storage, uri = _lane(tmp_path)
    settings = SimpleNamespace(krx_etf=None, minute_universe=None)

    with pytest.raises(SystemExit, match=r"\[krx_etf\] 섹션이 없다"):
        build_minute_universe.run(storage, settings, uri, RUN_ID)

    assert not Path(uri).exists()


def test_put_is_skipped_entirely_when_nothing_changed(tmp_path):
    # WHY: 위 no-op 테스트의 짝 — 레이크 쪽 PUT 도 안 늘어야 한다. 이 스텝은 canonical 을
    #      **읽기만** 하므로 레이크에 쓰는 경로가 생기면 그건 소유권 위반이다(레이크 파티션의
    #      writer 는 수집·정제 스텝이다).
    storage = _CountingStorage(tmp_path / "lake")
    _wide(storage, "2026-08-07", 20)
    baseline = storage.puts

    build_minute_universe.run(storage, _settings(), str(tmp_path / "u.json"), RUN_ID)

    assert storage.puts == baseline
