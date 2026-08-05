"""기관 세부 주체별 순매수 — "기관이 샀다" 를 검정 가능한 문장으로 쪼갠다.

이 저장소의 수급 계열족은 여태 외국인 하나만 봤다(`paneltest.FEATURES` 의
`("수급","누적") -> fl_cum20`, 원천은 `flow_daily.for_net`). 그런데 원장에는
기관 세부 주체가 **이미** 있었다: `s3_investor_flow` 의 `net_val_pension` ·
`net_val_investment_trust` · `net_val_financial_invest` 등 주체별 열 13종
(2026-08-04 실측 3,896행 · 365종목)과, 같은 어휘를 긴 형식으로 담은
`s3_investor_value`(1,356,004행 · 366종목 · 2025-06-02~2026-07-31).

주체를 뭉개면 안 되는 이유는 금융권 실무 그대로다. 연금이 산 것은 장기 배분
결정(자산배분 리밸런싱)이고 투신이 산 것은 펀드 자금 유출입의 결과다 - 전자는
지속성을 함의하고 후자는 환매가 오면 즉시 반대로 뒤집힌다. 금융투자(증권사
자기매매)는 상당 부분이 ELS·선물 헤지의 부산물이라 방향성 신호가 아니다.
이 셋을 `institution_total` 하나로 접으면 "기관 수급이 좋았습니다" 가 되는데,
그 문장은 무엇이 반증이 될지 특정할 수 없으므로 검정 불가능하다.

**원천을 둘 다 쓰는 이유**: `s3_investor_flow` 는 실측 11거래일
(2026-07-20~08-03)뿐이라 20일 누적조차 못 채운다. 이 표만 쓰면 도구가 항상
판정불가를 내는 죽은 도구가 된다. `s3_investor_value` 는 같은 주체 어휘
(DISTINCT investor_type 13종이 넓은 형식 열 이름과 1:1)로 290거래일을 덮으므로
둘을 합집합해 하나의 긴 형식 계열로 만든다. 겹치는 구간은 같은 KRX 공표치라
값이 같고, `max(nv)` 는 중복 제거용이다(값 선택이 아니다).

전역 상수 재사용: `vocab.MIN_N` 이 이력 하한이다(주체별 z 는 그 종목 자신의
과거 분포를 쓰므로 이력이 얇으면 z 가 의미를 잃는다). `EXPOSURE_CUT` ·
`PERMS` · `SEED` · `W_MINUTES` 는 이 도구에 해당 사항이 없다 - 여기엔 노출
상위 절단도, 순열 귀무도, 장중 창도 없다. 없는 절차의 상수를 끌어오면
"순열검정을 했다" 는 거짓 인상만 남는다.
"""
from __future__ import annotations

from .surface import Need, register
from .vocab import MIN_N

# KRX 가 `institution_total` 을 쪼개는 방식 그대로의 7 주체. 여기 없는
# `other_corp`(기타법인) · `other_org`(기타단체) · `other` 는 KRX 기준으로
# **기관이 아니다** - 이 도구의 주제(기관 세부 주체)에서 빠지는 것이 맞고,
# `foreign` · `individual` · `institution_total` 은 이미 fl_cum20 과
# paneltest.flow_z 가 보는 집계 버킷이라 여기서 다시 세지 않는다.
ACTORS: tuple[tuple[str, str], ...] = (
    ("financial_invest", "금융투자"),   # 증권사 자기매매 - 헤지 부산물이 섞인다
    ("investment_trust", "투신"),       # 공모펀드 - 자금 유출입의 결과
    ("pension", "연금"),                # 국민연금 등 - 장기 배분
    ("private_fund", "사모"),           # 헤지펀드·사모
    ("bank", "은행"),
    ("insurance", "보험"),              # 장기 부채 매칭
    ("merchant_bank", "종금"),
)

# z 창 60거래일 · 당일 제외. 새 임계가 아니라 `paneltest._FLOW_Z` 와
# `paneltest._INNOVATION` 이 이미 쓰는 계열 혁신 창과 **같은 값**이다 -
# 여기서 다른 창을 쓰면 같은 수급족의 z 가 도구마다 달라진다.
Z_WINDOW = 60

_WIDE = ", ".join(f"net_val_{a}" for a, _ in ACTORS)
_LITS = ", ".join(f"'{a}'" for a, _ in ACTORS)

# `_base(day)` 뒤에 붙는다 - 마지막 CTE 가 `g` 이므로 **선행 콤마**로 잇는다.
# 시점 규율: 모든 원천을 `trade_date < DATE '{day}'` 로 자른다. 투자자별 집계는
# 장 마감 후 18:00 KST 공표라 오늘 장중 움직임의 근거로 인용할 수 없다 - 그건
# 원인이 아니라 동시발생이다. `paneltest._FLOW_Z` 도크스트링이 세운 규율과 같고,
# 어제 수급은 오늘 개장 전에 이미 알려져 있으니 방아쇠 자격이 있다.
_SQL = """
, fa_wide AS (
    -- 넓은 형식. 주체가 열로 누워 있어 UNPIVOT 으로 한 번 편다 - 주체마다
    -- CTE 를 쓰면 열 추가가 코드 수정이 되고, 그러면 새 주체가 조용히 빠진다.
    SELECT trade_date, replace(actor, 'net_val_', '') AS actor, nv
    FROM (
        SELECT f.trade_date, {wide}
        FROM s3_investor_flow f
        JOIN v_instrument i ON i.ticker = f.ticker
        WHERE i.instrument_id = '{iid}' AND f.trade_date < DATE '{day}'
    ) w
    UNPIVOT (nv FOR actor IN ({wide}))
),
fa_long AS (
    -- 긴 형식(이력). investor_type 값이 넓은 형식 열 이름과 1:1 이라 그대로 붙는다.
    SELECT v.trade_date, v.investor_type AS actor, v.net_value AS nv
    FROM s3_investor_value v
    JOIN v_instrument i ON i.ticker = v.ticker
    WHERE i.instrument_id = '{iid}' AND v.trade_date < DATE '{day}'
),
fa AS (
    SELECT trade_date, actor, max(nv) AS nv
    FROM (SELECT * FROM fa_wide UNION ALL SELECT * FROM fa_long)
    WHERE actor IN ({lits}) AND nv IS NOT NULL
    GROUP BY 1, 2
),
den AS (
    -- 정규화 분모 = 거래대금(원). 절대 금액은 종목 간 비교가 안 된다 - 연금
    -- 300억은 대형주에선 잡음이고 소형주에선 유통물량이다. `fl_cum20` 이 쓰는
    -- 분모(창 내 평균 거래대금)와 **같은 규약**이고, net_val 도 원이라 무차원이 된다.
    -- v_pit 은 trade_date 파티션이 곧 PIT 클램프다(sql_surface.v_pit 주석).
    SELECT trade_date, turnover
    FROM v_pit
    WHERE instrument_id = '{iid}' AND trade_date < DATE '{day}' AND turnover > 0
),
cw AS (
    SELECT fa.actor, fa.trade_date,
           sum(fa.nv)        OVER w AS cum,
           avg(d.turnover)   OVER w AS den,
           count(*)          OVER w AS k,
           count(d.turnover) OVER w AS k_den,
           count(*)          OVER (PARTITION BY fa.actor) AS nd
    FROM fa LEFT JOIN den d ON d.trade_date = fa.trade_date
    WINDOW w AS (PARTITION BY fa.actor ORDER BY fa.trade_date
                 ROWS BETWEEN {w1} PRECEDING AND CURRENT ROW)
),
nz AS (
    SELECT actor, trade_date, nd, k, k_den, cum / NULLIF(den, 0) AS cum_norm FROM cw
),
zz AS (
    -- 그 종목 **자신의** 과거 대비 z. 수준만 주면 "많다/적다" 를 말할 수 없고,
    -- 횡단면 z 를 쓰면 종목 규모가 그대로 순위가 된다(연금은 대형주만 산다).
    SELECT actor, trade_date, nd, k, k_den, cum_norm,
           avg(cum_norm)         OVER r AS mu,
           stddev_samp(cum_norm) OVER r AS sd,
           count(cum_norm)       OVER r AS nh
    FROM nz
    WINDOW r AS (PARTITION BY actor ORDER BY trade_date
                 ROWS BETWEEN {zw} PRECEDING AND 1 PRECEDING)
)
SELECT actor, CAST(trade_date AS VARCHAR), nd, k, k_den, cum_norm,
       (cum_norm - mu) / NULLIF(sd, 0) AS z, nh
FROM zz
WHERE trade_date = (SELECT max(trade_date) FROM zz)
ORDER BY actor
"""


def _blank(reason: str, n_days: int = 0, note: str = "") -> dict:
    """부재의 표준형. 빈 dict·0·None 을 조용히 돌려주면 하류가 그것을 '기관 수급
    이상 없음' 으로 읽는다 - 부재는 기각이 아니다."""
    return {"verdict": "판정불가", "reason": reason, "n_days": n_days,
            "signed": None, "supports": None,
            "by_actor": {}, "top": "", "note": note}


@register("flow_detail",
          "기관을 연금·투신·보험·은행·사모·금융투자·종금으로 쪼개 주체별 20일 누적 "
          "순매수(거래대금 정규화)와 그 종목 자신의 60일 대비 z 를 낸다. "
          "'기관이 샀다' 를 누가 왜 샀는지로 바꾸는 자리 — **전일까지**만 쓴다.",
          needs=(Need("s3_investor_value", days=Z_WINDOW),), vocab=("수급",))
def _flow_detail(lake, *, day: str, instrument_id: str, window: int = 20,
                 **kw) -> dict:
    """주체별 (누적 정규화 수준, 자기 과거 대비 z).

    **왜 전일까지만 쓰는가**: 투자자별 매매 집계는 장 마감 후 18:00 KST 에
    공표된다. 오늘 수급을 오늘 움직임의 근거로 쓰면 아직 관측되지 않은 값을
    원인 자리에 놓는 것이고, 그 결과는 인과가 아니라 정의상 동시발생이다.
    `paneltest._FLOW_Z` 가 세운 규율과 같다. 어제 수급은 오늘 개장 전에 이미
    공표되어 있으므로 방아쇠 자격이 있다.

    **왜 수준과 z 를 같이 내는가**: 수준만 주면 "연금이 거래대금의 1.2% 를
    담았다" 가 많은 것인지 적은 것인지 말할 수 없다. z 만 주면 분모가 0 에
    가까운 종목에서 z 가 폭발한다. 둘을 나란히 놓아야 독자가 반증할 수 있다.

    **왜 주체별로 판정불가를 따로 내는가**: 어떤 종목은 연금은 잡히고 종금은
    아예 행이 없다(0 이 아니라 부재다). 하나가 없다고 전체를 침묵시키면 잴 수
    있었던 여섯 주체를 버리는 것이고, 반대로 부재를 0 으로 채우면 "종금은
    중립이었다" 는 없는 사실이 생긴다.
    """
    if not instrument_id:
        return _blank("instrument_id 없음 - 주체별 수급은 종목 축에서만 정의된다")
    if window < 2:
        return _blank(f"window={window} - 누적 창은 2거래일 이상이어야 한다")

    from .paneltest import _base
    q = _base(day) + _SQL.format(iid=instrument_id, day=day, wide=_WIDE,
                                 lits=_LITS, w1=window - 1, zw=Z_WINDOW)
    try:
        rows = lake.sql(q)
    except Exception as e:                  # noqa: BLE001 - 부재는 **사유와 함께**
        # 조용히 삼키면 터널 사망이 '기관 수급 이상 없음' 으로 위장된다.
        return _blank(f"수급 원천 조회 실패: {type(e).__name__}: {e}"[:200])
    if not rows:
        return _blank(f"{day} 이전 주체별 수급 행 없음 "
                      f"(s3_investor_flow ∪ s3_investor_value 에 이 종목이 없다)")

    seen = {r[0]: r for r in rows}
    n_days = max(int(r[2]) for r in rows)
    asof = str(rows[0][1])
    note = (f"기준일 {asof} (day={day} 당일 제외 · 18:00 공표 지연) · "
            f"누적 {window}거래일 / z {Z_WINDOW}거래일 · "
            f"분모=창내 평균 거래대금(원) · 원천 s3_investor_flow ∪ s3_investor_value")
    if n_days < MIN_N:
        return _blank(f"주체별 수급 이력 {n_days}거래일 < MIN_N={MIN_N} - "
                      f"자기 과거 대비 z 를 정의할 표본이 없다", n_days, note)

    by_actor: dict[str, dict] = {}
    for col, ko in ACTORS:
        r = seen.get(col)
        if r is None:
            by_actor[ko] = {"cum_norm": None, "z": None, "verdict": "판정불가",
                            "reason": f"{asof} 에 {col} 행 부재 (0 이 아니라 미관측)"}
            continue
        _, _, _, k, k_den, cum_norm, z, nh = r
        if int(k) < window:
            by_actor[ko] = {"cum_norm": None, "z": None, "verdict": "판정불가",
                            "reason": f"누적 창 미충족 {int(k)}/{window}거래일"}
        elif cum_norm is None:
            by_actor[ko] = {"cum_norm": None, "z": None, "verdict": "판정불가",
                            "reason": f"정규화 분모 결측 - 창 내 거래대금 {int(k_den)}일"}
        elif z is None:
            by_actor[ko] = {"cum_norm": float(cum_norm), "z": None,
                            "verdict": "판정불가",
                            "reason": f"z 창 표본 {int(nh)}일 · 분산 0 또는 이력 부족"}
        else:
            by_actor[ko] = {"cum_norm": float(cum_norm), "z": float(z),
                            "verdict": "계산됨", "reason": ""}

    ok = {k: v for k, v in by_actor.items() if v["verdict"] == "계산됨"}
    if not ok:
        why = " · ".join(f"{k}: {v['reason']}" for k, v in by_actor.items())
        return _blank(f"주체 {len(by_actor)}개 전부 판정불가 - {why}"[:400],
                      n_days, note) | {"by_actor": by_actor}
    # 동률 타이브레이크는 주체명 - 같은 입력이면 같은 top 이어야 한다(재실행 결정론).
    top = min(ok.items(), key=lambda kv: (-abs(kv[1]["z"]), kv[0]))[0]
    if len(ok) < len(by_actor):
        note += f" · 판정불가 주체 {len(by_actor) - len(ok)}개"
    # `signed` = **기관 7주체 누적의 합**. 주체 하나를 고르면 어느 주체인지에 따라
    # 부호가 갈려 '기관이 샀다' 라는 문장의 방향을 정할 수 없다. 합은 그 문장이
    # 실제로 말하는 양이다. 이 키가 없어서 신뢰성 검사가 수급 문장을 '부호 있는
    # 근거 없음' 으로 기각했다 - 재료는 있는데 신고를 안 한 것이다.
    tot = sum(v["cum_norm"] for v in by_actor.values()
              if isinstance(v.get("cum_norm"), (int, float)))
    return {"verdict": "계산됨", "reason": "", "n_days": n_days, "signed": tot,
            "supports": None, "inst_cum_norm": tot,
            "by_actor": by_actor, "top": top, "note": note}


__all__ = ["ACTORS", "Z_WINDOW"]
