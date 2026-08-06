"""DataGuide 947 항목을 **이름으로 골라 쓰는** 두 도구 — 목록(`dg_catalog`)과 측정(`dg_probe`).

왜 필요했나: 데이터가 아니라 **뷰가 병목이었다**. 설계 문서에 '노출 어휘 23개' 라고
적혀 있었지만 그건 사전의 크기가 아니라 `FEATURES`(paneltest) 에 손으로 박아 둔
조합의 수였다. `s3_dg_market` 뷰가 붙은 지금 사전은 947 항목이고 그중 실제로
그날 값이 있는 것은 **198종**(2026-07-31 실측)이다. 924 항목이 안 보였던 이유는
부재가 아니라 조회 경로의 부재였다.

왜 두 도구로 갈랐나: "무엇을 잴 수 있나" 와 "그 값이 얼마인가" 는 실패 방식이
다르다. 전자는 **사전에 있지만 데이터가 없는** 항목에서 거짓말을 하고(에이전트가
잴 수 없는 것을 가설로 세운다), 후자는 **범주형 값을 숫자로 읽는** 데서 거짓말을
한다(`'정상'`·`'일반'` 에 z 를 매긴다). 한 도구로 합치면 두 사유가 한 `reason`
문자열에 섞여 어느 쪽인지 사후에 못 가린다.

왜 피벗하지 않나: 롱 포맷(trade_date, ticker, item_code, value)에 일자 파티션이다.
`WHERE item_code = ?` 한 줄이면 파티션 프루닝 + 필터로 끝난다(실측 1.2초). 947 열로
피벗해 물질화하는 경로(`dgwide.build_mkt`)는 한 번도 완주하지 못했다 — 8,700 종목 ×
947 열 × 370 거래일을 만들 이유가, 한 번에 한 항목만 보는 이 도구에는 없다.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from .paneltest import MIN_N
from .surface import Need, register

# DataGuide 티커 표기는 `A` + 6자리(`A000660`)이고 우리 `v_instrument.ticker` 는
# `000660` 이다. 이 한 글자를 안 붙이면 모든 조회가 **조용히 0행**이 되고, 그건
# "그 종목엔 그 항목이 없다" 와 구분되지 않는다.
DG_PREFIX = "A"

# 사전 947 을 통째로 프롬프트에 붓지 않기 위한 상한. `limit` 이 이보다 커도 여기서
# 잘린다 - 상한 없는 덤프는 도구가 아니라 파일 출력이다.
MAX_ITEMS = 200

# 그날 값이 **숫자로 읽히는 비율**이 이보다 낮으면 그 항목은 범주형으로 본다.
# 실측(2026-07-31 횡단면): 범주형 4종은 숫자 파싱률이 정확히 0.000 이고 수치형
# 194종은 1.000 이다 - 중간값이 없으므로 0.5 는 어느 쪽으로도 여유가 크다.
# 왜 오늘 값 하나로 판정하지 않는가: 수치형 항목도 그 종목만 결측(`-`)일 수 있고,
# 그러면 '결측' 을 '범주형' 이라고 잘못 신고한다 - 사유가 틀리면 침묵보다 나쁘다.
NUM_FRAC = 0.5

# 범주 분포는 상위 3개만. 전량을 내면 8,700 행짜리 값 목록이 나온다.
TOP_CATS = 3

# `day` 가 휴장일일 수 있으므로 직전 거래일을 이 캘린더 창 안에서 찾는다. 연휴
# 최장(설·추석 + 주말)이 6일이라 10일이면 닫힌다. 창을 두는 이유는 파티션 프루닝
# 이다 - 창 없이 `max(trade_date)` 를 물으면 전 기간 스캔이 되어 48초가 걸린다(실측).
BACK_DAYS = 10


def _lit(s: str) -> str:
    """SQL 문자열 리터럴. item_code·ticker 는 에이전트가 주는 값이라 작은따옴표
    하나로 질의가 갈라질 수 있다 — 이스케이프해서 막는다."""
    return "'" + str(s).replace("'", "''") + "'"


def _num(v) -> float | None:
    """`value` 는 VARCHAR 다(실측 스키마). 못 읽는 값은 **None 으로 떨어뜨리되
    행을 버리지 않는다** — 얼마나 못 읽었는지가 범주형 판정의 근거이기 때문이다."""
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


@lru_cache(maxsize=1)
def _items() -> tuple[tuple[str, str, str, str], ...]:
    """(item_code, name_kr, domain, category) 947 항목. `dgwide.items_dict()` 는
    매번 S3 를 내려받으므로(실측 2.9초) 프로세스당 한 번만 문다. 튜플로 얼리는
    이유는 캐시된 리스트를 호출자가 변형하면 다음 호출이 오염되기 때문이다."""
    from .dgwide import items_dict
    return tuple(items_dict())


def _live_codes(lake, day: str) -> tuple[set[str], str, str]:
    """(그날 실재하는 item_code 집합, 실제로 잰 거래일, 오류사유).

    **사전에 있다 ≠ 데이터가 있다.** 사전 947 중 최근 거래일에 값이 오는 것은
    198종뿐이다(2026-07-31 실측). 이 교집합을 붙이지 않으면 에이전트는 749 개의
    잴 수 없는 항목을 가설 재료로 고르고, 그 가설은 전부 판정불가로 끝난다.
    """
    q = (f"WITH d AS (SELECT max(trade_date) AS td FROM s3_dg_market "
         f" WHERE trade_date BETWEEN DATE {_lit(day)} - INTERVAL {BACK_DAYS} DAY "
         f"                      AND DATE {_lit(day)}) "
         f"SELECT (SELECT td FROM d), item_code FROM s3_dg_market "
         f"WHERE trade_date = (SELECT td FROM d) "
         f"  AND trade_date BETWEEN DATE {_lit(day)} - INTERVAL {BACK_DAYS} DAY "
         f"                     AND DATE {_lit(day)} "
         f"GROUP BY 2")
    try:
        rows = lake.sql(q)
    except Exception as e:                  # noqa: BLE001 - 사유를 남긴다
        # 조용히 빈 집합을 돌려주면 터널·카탈로그 사망이 "오늘은 아무 항목도
        # 없었다" 로 위장된다. 그 둘은 전혀 다른 사건이다.
        return set(), "", f"{type(e).__name__}: {str(e)[:120]}"
    if not rows:
        return set(), "", (f"{day} 이전 {BACK_DAYS}일 안에 s3_dg_market 거래일이 없다")
    return {str(r[1]) for r in rows}, str(rows[0][0]), ""


@register("dg_catalog",
          "DataGuide 947 항목 사전을 domain/category 로 훑어 **무엇을 잴 수 있는지** "
          "낸다. query 로 이름·분류 부분일치(예 '배수'·'공매도') 검색하고, day 를 주면 그날 "
          "실제로 값이 오는 항목만 live 로 표시한다 — 사전에 있다고 잴 수 있는 게 아니다.",
          needs=(Need("s3_dg_market", days=1),), vocab=())
def _dg_catalog(lake, *, day: str = "", domain: str = "", query: str = "",
                limit: int = 40, **kw) -> dict:
    """사전을 검색 가능한 목록으로 낸다 — 그리고 **생략한 수를 말한다**.

    왜 상한을 두는가: 947 항목을 통째로 내면 프롬프트가 그것만으로 찬다. 왜 생략을
    말하는가: 침묵하면 에이전트는 받은 40개가 전부라고 믿고 "그런 항목은 없다" 를
    결론으로 쓴다 — 목록의 절단은 데이터의 부재가 아니다.

    왜 live 가 핵심인가: 사전은 curated 가 정의한 것이고 실재는 그날 파티션이
    말한다. 둘을 구분하지 않으면 "잴 수 있다" 는 주장이 사전을 근거로 서고, 그
    가설은 검정 단계에서 n=0 으로 죽는다 - 죽는 자리가 늦을수록 사유가 흐려진다.

    결정론: 정렬은 (live 먼저, domain, category, item_code) 고정이다. live 를 앞에
    두는 이유는 절단이 일어날 때 **잴 수 있는 것부터** 남기기 위해서다.
    """
    items = _items()
    n_dict = len(items)
    lim = max(1, min(int(limit), MAX_ITEMS))

    live, as_of, err = (set(), "", "")
    if day:
        live, as_of, err = _live_codes(lake, day)
        if err:
            return _nope_catalog(f"그날 실재 항목을 못 읽었다 — {err}", n_dict=n_dict)

    # `query` 는 이름 **과 분류** 둘 다에 건다. 이름만 걸었더니 '배수' 가 0건이었다
    # (실측): 주가배수 20종의 `name_kr` 은 'PER'·'PBR' 같은 약어이고 '배수' 라는
    # 말은 `category` 에만 있다. '공매도' 도 같다(차입공매도 12종). 에이전트가 쓰는
    # 검색어는 분류어라, 이름만 보는 검색은 사전이 비었다는 거짓 신호를 낸다.
    sel = [it for it in items
           if (not domain or it[2] == domain)
           and (not query or query in it[1] or query in it[3])]
    if not sel:
        return _nope_catalog(
            f"사전 {n_dict} 항목 중 조건(domain={domain or '전체'!r}, "
            f"query={query or '전체'!r}) 에 맞는 항목이 0개다 — 다른 말로 찾아라"
            f"(한국어 이름·분류에 대한 부분일치다). 분류는 "
            f"{sorted({f'{d}/{c}' for _, _, d, c in items})} 뿐이다",
            n_dict=n_dict, n_live=len(live) if day else None)

    groups: dict[str, int] = {}
    for _, _, dm, ct in sel:
        groups[f"{dm}/{ct}"] = groups.get(f"{dm}/{ct}", 0) + 1

    def _key(it):
        return (0 if it[0] in live else 1, it[2], it[3], it[0])

    ordered = sorted(sel, key=_key)
    rows = [{"code": c, "name": nm, "domain": dm, "category": ct,
             "live": (c in live) if day else None}
            for c, nm, dm, ct in ordered[:lim]]
    n_live_sel = sum(1 for it in sel if it[0] in live) if day else None

    note = (f"사전 {n_dict} · 조건 일치 {len(sel)} · 표시 {len(rows)}"
            + (f" · {as_of} 실재 {n_live_sel}/{len(sel)}" if day else
               " · day 미지정이라 실재 여부는 미확인(live=None)"))
    if day and (extra := len(live - {it[0] for it in items})):
        # 사전에 없는데 파티션엔 있는 코드. 사전이 낡았다는 신호라 침묵하면 안 된다.
        note += f" · 사전에 없는 실재 코드 {extra}개(사전이 낡았다)"

    return {"verdict": "계산됨", "reason": "", "n_dict": n_dict,
            "n_live": n_live_sel, "groups": dict(sorted(groups.items())),
            "items": rows, "omitted": len(sel) - len(rows),
            # 목록에는 방향이 없고, 어떤 주장도 지지/부정하지 않는다.
            "signed": None, "supports": None, "note": note}


def _nope_catalog(reason: str, **kw) -> dict:
    """부재는 **사유와 함께**. 빈 `items` 를 조용히 돌려주면 호출자는 그것을
    "그런 항목은 없다" 로 읽는데, 실제로는 검색어가 안 맞았거나 레이크가 죽은 것이다."""
    out = {"verdict": "판정불가", "reason": reason, "n_dict": 0, "n_live": None,
           "groups": {}, "items": [], "omitted": 0, "signed": None,
           "supports": None, "note": ""}
    out.update(kw)
    return out


def _nope_probe(reason: str, **kw) -> dict:
    """부재는 **사유와 함께**. z=0 이나 today=None 을 말없이 돌려주면 호출자가
    그것을 '변화 없음' 으로 읽는다 - 부재와 무변화는 다른 사건이다."""
    out = {"verdict": "판정불가", "reason": reason, "item_code": "", "name": "",
           "today": None, "prev": None, "chg": None, "z": None, "n": 0,
           "cs_pct_rank": None, "cs_n": 0, "kind": "", "signed": None,
           "supports": None, "note": ""}
    out.update(kw)
    return out


def _dg_ticker(t: str) -> str:
    """`000660` → `A000660`. 이미 DataGuide 표기면 그대로 둔다."""
    t = str(t).split(".")[0].strip()
    return t if t[:1] == DG_PREFIX and len(t) == 7 else DG_PREFIX + t


def _resolve(lake, day: str, instrument_id: str) -> tuple[str, str]:
    """instrument_id → ticker. `v_instrument` 는 상시 뷰가 아니라 `_base(day)` 가
    앞에 붙이는 PIT 클램프 CTE 다 — 빼먹으면 CatalogException 이다."""
    from .paneltest import _base
    try:
        rows = lake.sql(_base(day) + f"SELECT ticker FROM v_instrument "
                                     f"WHERE instrument_id = {_lit(instrument_id)}")
    except Exception as e:                  # noqa: BLE001 - 사유를 남긴다
        return "", f"instrument_id 해소 실패: {type(e).__name__}: {str(e)[:120]}"
    if not rows:
        return "", (f"instrument_id={instrument_id!r} 를 티커로 못 풀었다 — "
                    f"v_instrument 에 {day} 유효한 행이 없다")
    return str(rows[0][0]), ""


@register("dg_probe",
          "DataGuide 항목 하나(item_code)를 그 종목 그날에 대고 잰다: 오늘 값 · 자기 "
          "과거 창 대비 z · 그날 전 종목 횡단면 분위수 · 전일 대비 변화. 범주형 항목은 "
          "숫자로 바꾸지 않고 판정불가와 범주 분포를 낸다.",
          needs=(Need("s3_dg_market", days=2),), vocab=())
def _dg_probe(lake, *, day: str, instrument_id: str = "", item_code: str = "",
              ticker: str = "", window: int = 60, **kw) -> dict:
    """항목 하나를 두 축(자기 과거 · 그날 횡단면)으로 동시에 잰다.

    왜 두 축인가: z 만 주면 척도가 종목마다 달라 비교가 안 되고(거래대금 z=2 가
    대형주와 소형주에서 같은 뜻이 아니다), 횡단면 분위수만 주면 그 종목에게
    평소와 다른 일인지를 못 읽는다(대형주는 거래대금 분위수가 상시 상위다). 둘을
    같이 내면 "이 종목 기준으로도 드물고 전체 기준으로도 위" 인지가 갈린다.

    왜 오늘을 과거 분포에서 빼는가(PIT): 넣으면 자기로 자기를 검정하는 것이고,
    표본평균이 오늘 쪽으로 끌려가고 표준편차가 오늘의 편차만큼 부풀어 **z 가
    체계적으로 수축**한다. 극단일수록 수축이 커서 정확히 알고 싶은 날에 가장
    많이 틀린다.

    왜 범주형을 거절하는가: `value` 는 VARCHAR 이고 그중 4종(거래정지구분·
    관리감리구분·락구분·등락구분)은 `'정상'`·`'일반'` 같은 라벨이다(2026-07-31
    실측 숫자 파싱률 0.000). 억지로 캐스팅하면 파싱 실패가 0 이나 NaN 으로 접혀
    거짓 z 가 나오고, 그 z 는 다른 항목의 z 와 같은 형태라 사후에 구분되지 않는다.

    결정론: 순열도 표집도 없다. 같은 레이크·같은 인자면 같은 수가 나온다.
    """
    if not item_code:
        return _nope_probe("item_code 가 비었다 — dg_catalog 로 먼저 항목을 골라라")
    name = next((n for c, n, _, _ in _items() if c == item_code), "")
    if not name:
        # 사전에 없는 코드는 오타이거나 사전이 낡은 것이다. 조회해서 0행을 받고
        # "데이터가 없다" 라고 말하면 두 사유가 한 문장으로 뭉개진다.
        return _nope_probe(f"item_code={item_code!r} 는 사전 {len(_items())} 항목에 "
                           f"없다 — dg_catalog 의 code 를 그대로 써라",
                           item_code=item_code)

    tk = ticker
    if not tk:
        if not instrument_id:
            return _nope_probe("instrument_id·ticker 가 둘 다 비었다 — 항목 하나는 "
                               "종목 하나에 대고 재는 값이다",
                               item_code=item_code, name=name)
        tk, err = _resolve(lake, day, instrument_id)
        if err:
            return _nope_probe(err, item_code=item_code, name=name)
    dgt = _dg_ticker(tk)

    # 캘린더 창은 거래일 창의 2배 + 연휴 여유. 거래일은 캘린더의 약 68% 라
    # 2배면 `window` 개를 채우고도 남고, 창을 두어야 파티션이 잘린다.
    span = int(window) * 2 + BACK_DAYS
    q = (f"SELECT trade_date, value FROM s3_dg_market "
         f"WHERE ticker = {_lit(dgt)} AND item_code = {_lit(item_code)} "
         f"  AND trade_date BETWEEN DATE {_lit(day)} - INTERVAL {span} DAY "
         f"                     AND DATE {_lit(day)} "
         f"ORDER BY trade_date")
    try:
        series = lake.sql(q)
    except Exception as e:                  # noqa: BLE001 - 사유를 남긴다
        return _nope_probe(f"레이크 질의 실패: {type(e).__name__}: {str(e)[:120]}",
                           item_code=item_code, name=name)
    if not series:
        return _nope_probe(
            f"{dgt} 의 {item_code}({name}) 값이 {day} 이전 {span}일 안에 0행이다 — "
            f"사전에는 있으나 이 종목·이 기간엔 실재하지 않는다(부재이지 0 이 아니다)",
            item_code=item_code, name=name)

    as_of = str(series[-1][0])
    raw_today = series[-1][1]
    # PIT: 오늘은 분포 밖. 마지막 `window` 개만 쓴다 - 창을 안 자르면 캘린더 여유분
    # 때문에 요청한 창보다 긴 분포로 z 를 내게 되고 그 z 는 인자와 무관해진다.
    hist = series[:-1][-int(window):]

    # 범주형 판정은 **그날 횡단면**으로 한다: 한 종목의 한 값이 결측인 것과 항목
    # 자체가 라벨인 것은 다르고, 후자만이 z 를 금지할 사유다.
    try:
        cs = lake.sql(f"SELECT value FROM s3_dg_market "
                      f"WHERE item_code = {_lit(item_code)} "
                      f"  AND trade_date = DATE {_lit(as_of)}")
    except Exception as e:                  # noqa: BLE001 - 사유를 남긴다
        return _nope_probe(f"횡단면 질의 실패: {type(e).__name__}: {str(e)[:120]}",
                           item_code=item_code, name=name)
    cs_vals = [_num(r[0]) for r in cs]
    cs_num = [v for v in cs_vals if v is not None]
    frac = (len(cs_num) / len(cs_vals)) if cs_vals else 0.0
    stale = "" if as_of == day else f" · {day} 은 값이 없어 직전 거래일 {as_of} 로 쟀다"

    if not cs_vals:
        return _nope_probe(
            f"{as_of} 에 {item_code}({name}) 의 횡단면이 0행이다 — 그날 이 항목이 "
            f"적재되지 않았다", item_code=item_code, name=name, today=raw_today)

    if frac < NUM_FRAC:
        cnt: dict[str, int] = {}
        for r in cs:
            k = "(없음)" if r[0] is None else str(r[0])
            cnt[k] = cnt.get(k, 0) + 1
        top = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_CATS]
        return _nope_probe(
            f"이 항목은 **범주형**이다 — {as_of} 횡단면 {len(cs_vals)}건 중 숫자로 "
            f"읽히는 값이 {len(cs_num)}건({frac:.1%} < {NUM_FRAC:.0%})이라 z·분위수를 "
            f"내면 거짓 수가 된다",
            item_code=item_code, name=name, today=raw_today, kind="범주",
            cs_n=len(cs_vals),
            top_categories=[{"value": v, "n": n,
                             "share": round(n / len(cs_vals), 6)} for v, n in top],
            note=(f"상위 {len(top)}개 범주: "
                  + " · ".join(f"{v} {n}건" for v, n in top) + stale))

    today = _num(raw_today)
    if today is None:
        return _nope_probe(
            f"{as_of} 의 {dgt} {item_code}({name}) 값 {raw_today!r} 이 숫자로 읽히지 "
            f"않는다 — 항목은 수치형인데(횡단면 {frac:.1%} 파싱) 이 종목만 결측이다",
            item_code=item_code, name=name, kind="수치", cs_n=len(cs_vals),
            today=raw_today)

    x = np.array([v for r in hist if (v := _num(r[1])) is not None], dtype=float)
    n = int(x.size)
    prev = float(x[-1]) if n else None
    chg = (today - prev) if prev is not None else None

    z, why = None, ""
    if n < MIN_N:
        why = (f"과거 표본 n={n} < MIN_N={MIN_N} — z 침묵(부재이지 '변화가 "
               f"없다'는 뜻이 아니다)")
    else:
        sd = float(x.std(ddof=1))
        if sd == 0.0:
            # 상수 계열(예: 상장주식수)은 분모가 0 이다. inf 를 내면 그 항목이
            # 영원히 '최대 이상치' 로 뜬다 - 상수라는 사실을 말하는 게 맞다.
            why = f"과거 {n}일이 전부 같은 값({x[-1]:g}) — 표준편차 0 이라 z 부정의"
        else:
            z = round((today - float(x.mean())) / sd, 6)

    cs_arr = np.array(cs_num, dtype=float)
    cs_rank = round(float((cs_arr <= today).mean()), 6)

    note = (f"{name} · 자기 과거 {n}일(오늘 제외) · 횡단면 {len(cs_num)}/"
            f"{len(cs_vals)}종목" + (f" · {why}" if why else "") + stale)
    return {"verdict": "계산됨", "reason": "", "item_code": item_code, "name": name,
            "today": today, "prev": prev, "chg": None if chg is None else round(chg, 6),
            "z": z, "n": n, "cs_pct_rank": cs_rank, "cs_n": len(cs_num),
            "kind": "수치",
            # 전일 대비 변화는 **부호가 뜻을 갖는** 양이다(늘었나 줄었나).
            "signed": None if chg is None else round(chg, 6),
            # 이 도구는 값을 재기만 한다 - 어떤 주장을 지지/부정하는지는 판정하지 않는다.
            "supports": None, "note": note}


__all__ = ["BACK_DAYS", "DG_PREFIX", "MAX_ITEMS", "NUM_FRAC", "TOP_CATS"]
