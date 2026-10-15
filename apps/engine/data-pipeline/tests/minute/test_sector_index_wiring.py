"""업종지수를 1분 레인 다섯 번째 dataset 으로 넣으면서 함께 들어가는 가드 (ALPHA-887).

`test_inav_wiring.py` 가 형식 선례다. 이 dataset 은 **하이브리드**라 가드가 하나 더 있다:
코드 형상은 iNAV(window artifact·unit 집합·`MinuteWorkerLoop`)인데 universe 축은
뉴스·공시(소스 단위, `--universe` 거부)다. 그 조합이 처음이라 한 축만 맞추면 다른 축이
조용히 어긋난다 — 이 파일이 네 축을 각각 못박는다.
"""

from datetime import date

import pytest

from data_pipeline.lake.storage import (
    canonical_etf_inav_minute_prefix,
    canonical_price_minute_prefix,
    canonical_sector_index_minute_artifact_key,
    canonical_sector_index_minute_prefix,
)
from data_pipeline.minute.models import Universe, plan_session_windows
import re

from data_pipeline.minute.session_ops import _OPTIONAL_LANES, _resolve


def _minute_services_tf() -> str:
    """`minute_services.tf` 본문 — 저장소 체크아웃에서만 있다(설치 환경엔 없다)."""
    from pathlib import Path as _P
    here = _P(__file__).resolve()
    rel = "infra/terraform/modules/data-pipeline/minute_services.tf"
    try:
        return next((p / rel).read_text() for p in here.parents if (p / rel).exists())
    except StopIteration:
        pytest.skip("minute_services.tf 를 찾을 수 없음 — 저장소 체크아웃에서만 도는 계약 검사")
from data_pipeline.minute.states import (
    DATASET_SECTOR_INDEX_MINUTE,
    EXTENDED_HOURS_DATASETS,
    MINUTE_DATASETS,
    SCALED_DATASETS,
    SOURCE_GROUPS_BY_DATASET,
    UNIVERSE_DATASETS,
)


def test_선택_레인이_됐어도_구동_레인은_아니다():
    """축이 **셋**이다 — 어휘 / 선택 레인(자기 서비스 소유) / 구동 레인(스케일 권한).
    ALPHA-887 배선으로 이 dataset 이 **둘째까지** 왔고, 셋째는 여전히 아니다.

    ⚠️ 이 테스트는 원래 "선택 레인도 아니다"를 못박고 있었다(bounded 수동 실행).
    상주로 올리면서 그 절을 뒤집는 것이 이 변경의 본체다 — 다만 **뒤집으면 안 되는
    절이 같이 있다**: `SCALED_DATASETS` 는 그대로 밖이다. `_scale` 은 dataset 을 안 보고
    **공용 목록**을 내리므로, 이 세션으로 stop 을 부르면 살아 있는 price-worker 가 함께
    내려간다. 소유가 곧 스케일 권한이 아니라는 것이 뉴스·공시·iNAV 에서도 같다.
    """
    lane_datasets = {lane.dataset for lane in _OPTIONAL_LANES}
    assert DATASET_SECTOR_INDEX_MINUTE in MINUTE_DATASETS         # 어휘엔 있고
    assert DATASET_SECTOR_INDEX_MINUTE in lane_datasets           # 이제 서비스도 있고
    assert DATASET_SECTOR_INDEX_MINUTE not in SCALED_DATASETS     # 스케일 권한은 여전히 없다

    with pytest.raises(SystemExit, match="구동 레인이 아니다"):
        _resolve(DATASET_SECTOR_INDEX_MINUTE, "kis")


def test_워커_명령에_universe_를_주지_않는다():
    """🔴 주면 그 레인이 **매 거래일 통째로 안 선다.**

    planner 는 `UNIVERSE_DATASETS` 밖 dataset 에 `--universe` 가 오면 거부한다(exit≠0).
    `start_session_cli` 는 선택 레인 계획이 실패해도 가격 레인을 진행시키므로, 그 실패는
    로그 한 줄과 exit code 로만 남고 **가격 레인은 초록으로 돈다** — 이 레인만 조용히
    빠진 하루가 된다. 소급이 불가한 소스라 그 하루는 영구 결손이다.

    형제(inav-worker)는 `--universe` 를 **받는다**. 복사해 만들다 딸려 오기 딱 좋은
    자리라 값으로 못박는다.
    """
    text = _minute_services_tf()
    block = re.search(r"\n    sector-index-worker = \{.*?\n    \}\n", text, re.S)
    assert block, "sector-index-worker 서비스 블록을 못 찾았다 — 이 계약 검사가 헛돌고 있다"
    command = re.search(r"command\s*=\s*\[([^\]]*)\]", block.group(0))
    assert command, "sector-index-worker 의 command 를 못 찾았다"
    assert "--universe" not in command.group(1), \
        "sector-index-worker 에 --universe 가 붙었다 — planner 가 거부해 이 레인이 매일 안 선다"
    assert "sector-index-worker" in command.group(1)


def test_워커가_KIS_자격증명을_블록_안에서_받는다():
    """`sector_index_worker_cli` 가 `settings.kis_nav` 없으면 기동에서 죽는다(fail-loud).

    ⚠️ **블록 단위로 본다** — 파일 어딘가(inav-worker 블록)에 같은 비밀값이 있는 것으로는
    이 서비스가 받는다는 증거가 안 된다. 새 상주 서비스가 `local.env` 를 상속하지 않아
    조용히 구멍이 났던 선례가 바로 옆에 있다(#642 봇 P2, inav-worker 의 OPS_KR_HOLIDAYS).

    토큰 캐시도 같이 본다 — KIS 앱키는 **전역 한도**라, 빠지면 매 기동 발급이 분당 1회
    제한에 걸려 가격 레인과 다툰다(ALPHA-573).
    """
    text = _minute_services_tf()
    block = re.search(r"\n    sector-index-worker = \{.*?\n    \}\n", text, re.S).group(0)
    # ⚠️ **이름만 세지 않는다.** 이름의 존재는 계약보다 약한 단언이다 — `APP_KEY` 가
    # `:app_secret::` 을 가리켜도 이름과 `=` 는 그대로 남아 통과한다(Rule 9: 단언이
    # 지키려는 계약의 반례를 실제로 거부해야 한다). 그래서 **가리키는 곳까지** 본다.
    expected = {
        "DATA_PIPELINE_KIS_NAV__SOURCE__APP_KEY": r"secret\.kis\.arn\}:app_key::",
        "DATA_PIPELINE_KIS_NAV__SOURCE__APP_SECRET": r"secret\.kis\.arn\}:app_secret::",
        "DATA_PIPELINE_DB__PASSWORD": r"db_password_secret_arn\}:password::",
    }
    for name, target in expected.items():
        # `NAME =` 를 그대로 찾지 않는다 — terraform fmt 가 `=` 를 형제 키 길이에 맞춰
        # 정렬해 공백 수가 배선과 무관하게 바뀐다.
        found = re.search(rf"{name}\s*=\s*\"([^\"]+)\"", block)
        assert found, f"sector-index-worker 블록에 {name} 이 없다 — 기동에서 죽는다"
        assert re.search(target, found.group(1)), \
            f"{name} 이 엉뚱한 곳을 가리킨다: {found.group(1)} — 기동하거나 남의 값을 받는다"
    # 토큰 캐시도 **가리키는 곳까지** 본다. 이름만 세면 다른 파라미터를 가리켜도 통과하는데,
    # task role 은 정본 파라미터만 허용하므로 실물에서는 AccessDenied 뒤 개별 발급으로
    # 폴백한다 — 캐시가 있는 줄 알았는데 분당 1회 제한을 가격 레인과 다투는 상태가 된다
    # (있으나 마나가 가장 나쁘다).
    assert re.search(r"KIS_TOKEN_CACHE_PARAM\s*=\s*local\.kis_token_param_name", block), \
        ("sector-index-worker 의 토큰 공유 캐시가 정본(local.kis_token_param_name)을 "
         "안 가리킨다 — 매 기동 발급이 분당 1회 제한에 걸리고 가격 레인과 다툰다")


def test_기대_집합이_universe_가_아니다():
    """🔴 지수 45종은 universe.json 에 **없다** — ETF 명부에도 구성종목에도 없다.

    `UNIVERSE_DATASETS` 에 넣으면 planner 가 `--universe` 를 요구하고, 그 파일로 세운
    기대 집합에는 업종코드가 한 줄도 없어 매 window 가 빈 성공(VALID_EMPTY)으로
    확정된다. 정본은 `[minute_sector_index.index_map]` 이다.
    """
    assert DATASET_SECTOR_INDEX_MINUTE not in UNIVERSE_DATASETS


def test_격자는_시간외로_넓히지_않는다():
    """정규장 390 window 다.

    이 TR 은 정규장 지수만 준다 — 시간외로 넓히면 매 거래일 330 window 가 아무도 못
    채운 채 DUE 로 남고, 이 소스는 소급이 불가라(소급 TR 은 일봉으로 degrade) 영구
    결손이다. iNAV 를 시간외에서 막은 것과 결론은 같고 근거만 다르다.
    """
    universe = Universe(
        universe_version="v1", etf_ids=("069500",), constituent_ids=("005930",),
        extended_hours_ids=("069500",),
    )
    day = date(2026, 8, 10)

    extended = plan_session_windows(day, universe=universe, extended_hours=True)
    # 이 dataset 의 실제 격자 — universe 없이 세운다(planner 가 그렇게 부른다)
    regular = plan_session_windows(day, universe=None, extended_hours=False)

    assert len(extended) == 720   # 08:00~20:00
    assert len(regular) == 390    # 09:00~15:30
    assert regular[0][0].hour == 9
    assert DATASET_SECTOR_INDEX_MINUTE not in EXTENDED_HOURS_DATASETS


def test_소스는_KIS_하나다():
    """KRX 는 지수 분봉 API 를 안 열고(일봉만), 토스에는 지수 축이 없다."""
    assert SOURCE_GROUPS_BY_DATASET[DATASET_SECTOR_INDEX_MINUTE] == frozenset({"kis"})


def test_canonical_키가_프리픽스에서_자라고_남의_dataset_과_갈린다():
    """prefix 는 스캔 축, key 는 쓰기 축 — 조립이 두 곳이면 한쪽만 옮겨진다.

    분봉·iNAV 와 섞으면 그 window 가 "종목은 다 왔는데 지수는 안 왔다"를 표현할 수 없어
    매번 INCOMPLETE 다(기대 집합이 다르기 때문이다).
    """
    prefix = canonical_sector_index_minute_prefix("KR", "2026-08-10")
    key = canonical_sector_index_minute_artifact_key("KR", "2026-08-10", "0931", 1)

    assert key.startswith(prefix)
    assert prefix != canonical_price_minute_prefix("KR", "2026-08-10")
    assert prefix != canonical_etf_inav_minute_prefix("KR", "2026-08-10")
    # 정정은 새 generation → 새 key 라 원본을 안 덮는다. run_id 는 없다(재실행 no-op).
    assert key != canonical_sector_index_minute_artifact_key("KR", "2026-08-10", "0931", 2)
    assert key == canonical_sector_index_minute_artifact_key("KR", "2026-08-10", "0931", 1)
    # ⚠️ **window 축을 명시로 못박는다** — 이 단언이 없으면 키에서 `window=` 세그먼트를
    # 통째로 빼도 위 단언들이 전건 통과한다(iNAV 에서 실제로 그랬다). 그러면 하루
    # 390 window 가 한 키를 다툰다.
    assert "window=0931" in key
    assert key != canonical_sector_index_minute_artifact_key("KR", "2026-08-10", "0932", 1)
