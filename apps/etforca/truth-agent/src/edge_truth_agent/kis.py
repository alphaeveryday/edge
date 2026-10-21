"""한국투자증권(KIS) 시세 — 오늘의 급등주용. truth 전용 앱키를 쓴다(데이터 파이프라인 키와 섞지 않는다).

- 토큰은 실행마다 한 번 발급해 메모리에만 둔다(앱키당 분당 1회 발급 제한, 24시간 유효). 디스크·로그에 남기지 않는다.
- 등락률 순위 `FHPST01700000`(시장별 상승률 상위), 당일 분봉 `FHKST03010200`(한 콜 = 30분, 최신→과거).
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://openapi.koreainvestment.com:9443"
MARKETS = {"KOSPI": "0001", "KOSDAQ": "1001"}  # 등락률 순위의 시장 구분 코드


class KisError(RuntimeError):
    """KIS 호출 실패(사유 포함)."""


@dataclass(frozen=True)
class Mover:
    code: str
    name: str
    market: str
    price: float
    prev_close: float
    change_pct: float
    volume: int


# ETF·ETN·스팩은 이름으로 거른다(순위 응답에 상품 구분이 없다). 우선주는 코드 끝자리로 거른다(보통주는 0).
_EXCLUDE_WORDS = ("ETN", "ETF", "스팩", "KODEX", "TIGER", "KBSTAR", "RISE", "ARIRANG", "HANARO", "SOL ", "ACE ", "PLUS ", "KOSEF",
                  "TIMEFOLIO", "TREX", "히어로즈", "마이다스", "파워 ", "UNICORN", "WON ", "BNK ", "1Q ", "VITA", "에셋플러스",
                  "KIWOOM", "KoAct", "TIME ", "ITF ", "마이티", "레버리지", "인버스", "선물")


def is_common_stock(code: str, name: str) -> bool:
    """보통주인가(ETF·ETN·스팩·우선주 제외). 우선주는 코드 끝자리가 0 이 아니다. 새 영숫자 코드도 받는다."""
    return len(code) == 6 and code.isalnum() and code.endswith("0") and not any(w in name for w in _EXCLUDE_WORDS)


def _num(value, default=0.0) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


class Kis:
    def __init__(self, app_key: str, app_secret: str, *, timeout: float = 15.0):
        self.app_key, self.app_secret, self.timeout = app_key, app_secret, timeout
        self._token: str | None = None

    # ── 인증 ──
    def token(self) -> str:
        if self._token is None:
            body = json.dumps({"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret}).encode()
            req = urllib.request.Request(BASE + "/oauth2/tokenP", data=body, headers={"content-type": "application/json"})
            data = self._open(req, "토큰 발급")
            if not data.get("access_token"):
                raise KisError(f"토큰 발급 응답에 토큰이 없습니다: {data.get('error_code') or data.get('msg1') or '사유 미상'}")
            self._token = str(data["access_token"])
        return str(self._token)

    def _open(self, req: urllib.request.Request, what: str) -> dict:
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as res:
                    return json.loads(res.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", "replace")[:200]
                if error.code in (429, 500, 502, 503) and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise KisError(f"{what} HTTP {error.code}: {detail}") from error
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise KisError(f"{what} 연결 실패: {error}") from error
        raise KisError(f"{what} 실패")  # 도달하지 않는다

    def _get(self, path: str, tr_id: str, params: dict) -> dict:
        headers = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {self.token()}",
                   "appkey": self.app_key, "appsecret": self.app_secret, "tr_id": tr_id, "custtype": "P"}
        req = urllib.request.Request(f"{BASE}{path}?{urllib.parse.urlencode(params)}", headers=headers)
        data = self._open(req, tr_id)
        if str(data.get("rt_cd")) != "0":
            raise KisError(f"{tr_id} 실패: {data.get('msg_cd')} {data.get('msg1')}")
        time.sleep(0.07)  # 초당 호출 제한(실전 20건) 아래로
        return data

    # ── 등락률 순위 ──
    def risers(self, market: str, limit: int = 30) -> list[Mover]:
        """시장별 상승률 상위(보통주만)."""
        params = {
            "fid_cond_mrkt_div_code": "J", "fid_cond_scr_div_code": "20170", "fid_input_iscd": MARKETS[market],
            "fid_rank_sort_cls_code": "0", "fid_input_cnt_1": "0", "fid_prc_cls_code": "0", "fid_input_price_1": "",
            "fid_input_price_2": "", "fid_vol_cnt": "", "fid_trgt_cls_code": "0", "fid_trgt_exls_cls_code": "0",
            "fid_div_cls_code": "0", "fid_rsfl_rate1": "", "fid_rsfl_rate2": "",
        }
        rows = self._get("/uapi/domestic-stock/v1/ranking/fluctuation", "FHPST01700000", params).get("output") or []
        out = []
        for row in rows:
            code, name = str(row.get("stck_shrn_iscd", "")).strip(), str(row.get("hts_kor_isnm", "")).strip()
            price, diff = _num(row.get("stck_prpr")), _num(row.get("prdy_vrss"))
            if not code or not name or price <= 0 or not is_common_stock(code, name):
                continue
            out.append(Mover(code=code, name=name, market=market, price=price, prev_close=price - diff,
                             change_pct=_num(row.get("prdy_ctrt")), volume=int(_num(row.get("acml_vol")))))
        return out[:limit]

    # ── 당일 분봉 → 5분 종가 ──
    def series(self, code: str, until_hhmm: str, day_ymd: str) -> list[list]:
        """그날(day_ymd) 09:00~until 의 5분 간격 [HH:MM, 종가]. 한 콜이 30분치라 30분씩 거슬러 받는다.
        휴장일에는 KIS 가 전 거래일 분봉을 주므로 날짜가 다른 행은 버린다(빈 목록 = 그날 거래 없음)."""
        closes: dict[str, float] = {}
        hour = until_hhmm.replace(":", "") + "00"
        for _ in range(14):  # 09:00~15:30 = 13콜이면 충분
            # PW_DATA_INCU_YN=Y: 데이터 파이프라인 실측(kis_minute.py) — N 이면 창 앞부분이 빈다
            rows = self._get("/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", "FHKST03010200", {
                "FID_ETC_CLS_CODE": "", "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                "FID_INPUT_HOUR_1": hour, "FID_PW_DATA_INCU_YN": "Y"}).get("output2") or []
            labels = [str(r.get("stck_cntg_hour", "")) for r in rows if len(str(r.get("stck_cntg_hour", ""))) == 6]
            for r in rows:
                t, p = str(r.get("stck_cntg_hour", "")), _num(r.get("stck_prpr"))
                if len(t) == 6 and p > 0 and "090000" <= t <= "153000" and str(r.get("stck_bsop_date", day_ymd)) == day_ymd:
                    closes.setdefault(t[:4], p)
            earliest = min(labels) if labels else ""
            if not earliest or earliest <= "090000" or earliest >= hour:
                break  # 다 받았거나, 더 거슬러 가지 못한다(같은 콜 반복 방지)
            hour = earliest  # 다음 콜은 이번에 받은 가장 이른 시각부터
        return five_minute(closes)


def five_minute(closes: dict[str, float]) -> list[list]:
    """1분 종가(HHMM→가격) → 5분 구간 [구간 끝 HH:MM, 그 구간 마지막 종가] (시각 오름차순).
    라벨을 구간 끝으로 단다 — 09:00~09:04 의 마지막 가격은 09:05 시점 가격이다."""
    buckets: dict[str, tuple[str, float]] = {}
    for hhmm, price in closes.items():
        minute = int(hhmm[:2]) * 60 + int(hhmm[2:])
        end = minute - minute % 5 + 5
        key = f"{end // 60:02d}:{end % 60:02d}"
        if key not in buckets or hhmm > buckets[key][0]:
            buckets[key] = (hhmm, price)
    return [[k, v[1]] for k, v in sorted(buckets.items())]
