"""장중 iNAV 의 1분 레인 계약 — dataset 어휘와 canonical 키 (ALPHA-845).

아직 Worker 가 없다(배선 0). 그래서 여기서 지키는 것은 **다음 조각이 밟을 계약**이다:
키 조립이 한 곳에서만 나오는가, 세대가 기존 artifact 를 덮지 않는가, 분봉과 섞이지
않는가, 세션이 universe 를 요구하는가. 넷 다 어기면 조용히 틀린다 — 스캐너가 없는
prefix 를 훑어 "구멍 없음"을 확정하거나, 정정이 원본을 덮거나, 구성종목 329종이 매
window 결손으로 잡힌다.
"""

import pytest

from data_pipeline.lake.storage import (
    canonical_etf_inav_minute_artifact_key,
    canonical_etf_inav_minute_prefix,
    canonical_price_minute_prefix,
)
from data_pipeline.minute.session_cli import _load_universe
from data_pipeline.minute.states import (
    DATASET_ETF_INAV_MINUTE,
    DATASET_PRICE_MINUTE,
    SOURCE_GROUPS_BY_DATASET,
)


def test_키가_프리픽스에서_자란다():
    """prefix 는 orphan 스캔 축이고 key 는 쓰기 축이다 — 조립이 두 곳이면 한쪽만 옮겨져
    스캐너가 없는 경로를 훑고 빈 목록을 clean 으로 확정한다."""
    prefix = canonical_etf_inav_minute_prefix("KR", "2026-08-10")
    key = canonical_etf_inav_minute_artifact_key("KR", "2026-08-10", "0931", 1)

    assert key.startswith(prefix)


def test_세대가_경로_축이라_정정이_원본을_안_덮는다():
    """불변 artifact 계약: correction 은 새 generation → 새 key 다. generation 이 경로에
    없으면 정정이 같은 키에 다른 바이트를 써서 원본이 사라지고, "같은 checksum → 재사용"
    복구 판정도 함께 무너진다."""
    first = canonical_etf_inav_minute_artifact_key("KR", "2026-08-10", "0931", 1)
    corrected = canonical_etf_inav_minute_artifact_key("KR", "2026-08-10", "0931", 2)

    assert first != corrected
    # run_id 가 없다 — 같은 인자면 같은 키여야 "PUT 후 commit 전 종료 → 재실행이 같은
    # 키에 같은 바이트" 복구가 성립한다.
    assert first == canonical_etf_inav_minute_artifact_key("KR", "2026-08-10", "0931", 1)


def test_분봉과_프리픽스가_갈린다():
    """같은 (market, session_date, window) 라도 담는 것과 **기대 집합**이 다르다 —
    구성종목에는 NAV 가 없다. 한 artifact 에 섞으면 "봉은 다 왔는데 NAV 는 ETF 것만
    왔다"를 표현할 수 없어 그 window 가 매번 INCOMPLETE 가 된다."""
    inav = canonical_etf_inav_minute_prefix("KR", "2026-08-10")
    price = canonical_price_minute_prefix("KR", "2026-08-10")

    assert inav != price
    assert not inav.startswith(price) and not price.startswith(inav)


def test_iNAV_소스는_KIS_하나다():
    """토스 분봉 API 에는 NAV 축이 없다(`1m`·`1d` 캔들만). 어휘 밖 소스로 세션이 서면
    그 벤더의 어댑터가 없어 하루가 조용히 안 돈다."""
    assert SOURCE_GROUPS_BY_DATASET[DATASET_ETF_INAV_MINUTE] == frozenset({"kis"})


def test_iNAV_세션은_universe_를_요구한다():
    """universe 없이 흘리면 정규장 window 만 계획되고 시간외 구간이 **아무 실패 신호
    없이** 누락된다(가격 세션과 같은 축). iNAV 는 소급이 영구 불가라 그 누락이 그날로
    확정된다."""
    with pytest.raises(ValueError, match="universe"):
        _load_universe(DATASET_ETF_INAV_MINUTE, None)


def test_universe_를_안_쓰는_dataset_에_주면_거부한다():
    """어휘를 늘릴 때 반대 방향도 함께 지킨다 — 뉴스처럼 universe 를 안 쓰는 dataset 에
    파일을 주면 그 값이 조용히 무시돼, 잘못된 유니버스로 돌고 있다고 오해하게 된다."""
    with pytest.raises(ValueError, match="universe"):
        _load_universe("news_minute", "s3://bucket/universe.json")

    assert _load_universe("news_minute", None) is None
    assert DATASET_PRICE_MINUTE in SOURCE_GROUPS_BY_DATASET  # 기존 어휘 불변
