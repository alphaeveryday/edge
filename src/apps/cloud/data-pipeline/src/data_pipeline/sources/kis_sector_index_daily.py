"""KIS(한국투자) KRX 업종지수 일봉 소스 어댑터 (ALPHA-1254 — sector_index_daily 원본저장).

API: 국내업종 기간별시세(일/주/월/년) `inquire-daily-indexchartprice`, tr_id `FHKUP03500100`.

분봉 레인(`kis_sector_index`, `FHKUP03500200`)으로는 그날 공식 종가를 얻을 수 없다. 격자가
`[15:29,15:30)` 에서 끝나고, 격자 밖 `153000` 봉도 공식 종가가 아니다(2026-10-08 실측: 코스피
`0001` 의 `153000` close 6644.27, 공식 종가 6625.93 = 이 TR = Yahoo `^KS11`). 그래서 종가는
이 일봉 TR 로 따로 받는다.

일별 NAV(`kis_nav.KisNavSource`)와 형상이 같아 그 어댑터를 상속하고 `ingest_raw_etf` 스텝을
그대로 쓴다. 갈리는 축은 셋이다(2026-10-08 실측):

1. 수집 대상은 `[minute_sector_index.index_map]`(KRX 업종코드 → KIS `U` 코드)이다 — 분봉 레인과
   같은 정본이다. KIS 코드는 KRX 코드가 아니라서(`kis_sector_index` 도크스트링 1번) 번역은
   여기서 끝나고, raw 의 `index_code` 는 KRX 업종코드다.
2. 응답 배열이 `output2` 이고 **최신순 최대 50행**이다. `tr_cont` 가 비어 다음 페이지 신호가
   없으므로 `FID_INPUT_DATE_2` 를 받은 행 중 가장 이른 거래일의 전날로 옮겨 다시 묻는다
   (07-01~07-24 창이 17행으로 온 것을 확인했다).
3. 필드는 `stck_bsop_date`·`bstp_nmix_prpr`(종가)·`bstp_nmix_oprc/hgpr/lwpr`·`acml_vol`·
   `acml_tr_pbmn`·`mod_yn` 이다. 수치 해석은 canonical 소관이다(bronze 무변형).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..config import KisNavSource as KisNavSourceConfig
from .http import PoliteClient
from .kis_nav import EmptyOutputError, KisNavSource
from .kis_sector_index import MARKET_DIV_INDEX

TR_ID_INDEX_DAILY = "FHKUP03500100"
PATH_INDEX_DAILY = "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"
# 한 응답의 최대 행 수(실측). 이보다 적게 오면 창이 끝난 것이다.
PAGE_ROWS = 50
# 지수 하나의 페이지 상한 — 1,500거래일(약 6년). 넘으면 창 절단이라 실패로 드러낸다.
MAX_PAGES = 30


class KisSectorIndexDailySource(KisNavSource):
    """KIS 업종지수 일봉 어댑터 — index_map 이 곧 유니버스다."""

    tr_id = TR_ID_INDEX_DAILY
    path = PATH_INDEX_DAILY
    output_key = "output2"
    unit_field = "index_code"

    def __init__(
        self,
        config: KisNavSourceConfig,
        index_map: dict[str, str],
        client: PoliteClient,
        from_date: str | None = None,
        to_date: str | None = None,
    ):
        super().__init__(config, {}, client, from_date, to_date)
        # 사본을 든다 — 호출자가 나중에 고쳐도 도는 중의 질의 대상이 바뀌면 안 된다.
        self.index_map = dict(index_map)

    def plan(self) -> list[tuple[str, str]]:
        """수집 대상 → [(KRX 업종코드, KIS 지수코드)]. 맵 검증은 config 가 이미 했다."""
        return sorted(self.index_map.items())

    def _query_params(self, kis_symbol: str, d1: str, d2: str) -> dict[str, str]:
        return {
            "FID_COND_MRKT_DIV_CODE": MARKET_DIV_INDEX,
            "FID_INPUT_ISCD": kis_symbol,
            "FID_INPUT_DATE_1": d1,
            "FID_INPUT_DATE_2": d2,
            "FID_PERIOD_DIV_CODE": "D",
        }

    def _row_defect(self, row: object) -> str | None:
        """거래일이 없는 행은 어느 날 값인지 모르고 페이지도 넘길 수 없다 — 격리한다."""
        defect = super()._row_defect(row)
        if defect is not None:
            return defect
        day = row.get("stck_bsop_date")  # type: ignore[union-attr]
        if not (isinstance(day, str) and len(day) == 8 and day.isdigit()):
            return f"거래일 없는 행: stck_bsop_date={day!r}"
        return None

    def _fetch_etf(
        self, our_etf_id: str, kis_symbol: str, d1: str, d2: str, token: str
    ) -> list[dict]:
        """창 전체를 최신 → 과거로 50행씩 넘겨 받는다. 반환은 거래일 오름차순이다.

        두 번째 페이지부터의 빈 응답은 "창에 거래일이 더 없다"는 정상 종료다. 첫 페이지가
        50행으로 차고 창 시작일이 주말·휴장일이면 다음 창에 거래일이 하나도 없어서 생긴다.
        첫 페이지의 빈 응답은 부모와 같이 실패다(잘못된 코드거나 거래일 없는 창).
        """
        rows: dict[str, dict] = {}
        end = d2
        for _ in range(MAX_PAGES):
            try:
                page = super()._fetch_etf(our_etf_id, kis_symbol, d1, end, token)
            except EmptyOutputError:
                if rows:
                    break
                raise
            new = 0
            for row in page:
                if row["stck_bsop_date"] not in rows:
                    rows[row["stck_bsop_date"]] = row
                    new += 1
            # 하한 없는 창(d1="")은 KIS 가 최근 50행만 준다 — 더 넘길 기준이 없다.
            if not page or not d1 or new == 0 or len(page) < PAGE_ROWS:
                break
            earliest = min(row["stck_bsop_date"] for row in page)
            if earliest <= d1:
                break
            end = (datetime.strptime(earliest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        else:
            raise ValueError(f"MAX_PAGES({MAX_PAGES}) 도달 — 창 절단 가능(구간을 좁혀 재실행)")
        return [rows[day] for day in sorted(rows)]
