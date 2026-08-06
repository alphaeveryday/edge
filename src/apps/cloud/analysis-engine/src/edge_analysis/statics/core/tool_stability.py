"""기간 안정성 검정 — "이 관계가 기간을 갈라도 재현되는가".

표본 크기 다음으로 금융권이 묻는 질문이고, 여기서 깨지면 그 주장은 납품할 수
없다. 전체 패널로 p=0.01 을 얻어도 그 효과가 **한 기간에만** 있었다면 그것은
관계가 아니라 그 시기의 사건이다 - 그런데 패널 게이트는 단일 τ 하나만 내므로
그 구분이 산출물에 남지 않는다.

이 저장소는 국면이 갈렸다는 것을 이미 실측했다: 시장 20일 수익 sd 가 2026년
5.46% 인데 2022-25년은 1.3~2.1% 다(`FEATURES` 의 `국면/수준` 주석, KRX 독립
소스 확인). 패널 268거래일의 84% 가 다른 국면이다. 그 표본으로 추정한 하나의
τ 를 오늘 셀에 적용하는 것이 지금 배선의 최대 위험인데, **그 위험을 재는
도구가 없었다**. 조절자(`국면/수준`)는 국면별 CATE 를 주지만 그건 "지금 국면에서
얼마냐" 이고, 이 도구는 "애초에 같은 관계였냐" 를 따로 묻는다 - 뒤집힌 관계는
CATE 가 매끄럽게 보간해 감춰버린다.
"""
from __future__ import annotations

import numpy as np

from .paneltest import (ALPHA, EXPOSURE_CUT, FEATURES, LAYER_EXPOSURES,
                        LAYER_Y, MIN_N, _base, _panel_rows, _POINT_PANEL,
                        _pctile, _stratified_p, _two_sided)
from .surface import register

# 효과 크기 비율이 이보다 작으면 부호가 같아도 "재현" 이라 부르지 않는다.
# 새 임계를 발명한 게 아니라 **경고 문구의 문턱**일 뿐이다 - 판정(재현/뒤집힘)은
# 부호와 MIN_N 만으로 갈린다. 1/3 은 국면 sd 격차(5.46% vs 1.3~2.1% ≈ 2.6~4.2배)의
# 역수 대역에서 왔다: 그 정도 차이는 국면 변동성만으로도 설명되므로 "같은 크기" 라
# 주장할 근거가 없다.
RATIO_WARN = 1.0 / 3.0


def _no(reason: str, **extra) -> dict:
    """판정불가는 **사유와 함께만** 나간다. 부재를 '효과 없음' 으로 읽히게 만드는
    빈 dict·0·None 반환이 이 저장소가 가장 싫어하는 실패다."""
    out = {"verdict": "판정불가", "reason": reason, "stable": "판정불가",
           "n_early": 0, "n_late": 0, "eff_early": None, "eff_late": None,
           "p_early": None, "p_late": None, "ratio": None, "split_date": "",
           "note": "이 축은 **검토되지 않았다** (효과 없음이 아니다)"}
    out.update(extra)
    return out


def _side(ar: np.ndarray, xv: np.ndarray, dates: np.ndarray) -> tuple[float, float]:
    """한 조각의 (상위-하위 노출군 평균차, 양측 p).

    절단은 전역 `EXPOSURE_CUT` 을, p 는 날짜 층화 순열(`_stratified_p`, SEED 고정)을
    쓴다. 조각마다 다른 절단을 쓰면 두 조각의 차이가 관계의 불안정인지 절단의
    차이인지 갈리지 않는다.

    양측인 이유: 부호를 사후에 보므로 단측 p 는 방향 선택의 자유도를 숨긴다.
    """
    hi = _pctile(xv) >= EXPOSURE_CUT
    p1 = _stratified_p(ar, hi, dates)
    return float(ar[hi].mean() - ar[~hi].mean()), _two_sided(p1)


@register("stability", "한 (사건타입 × 노출) 관계를 사건일 중앙값으로 전·후 두 기간으로 "
                       "갈라 각각 재검정하고, 부호가 재현되는지 뒤집히는지와 효과 크기 "
                       "비율을 낸다. 전 기간 p 하나로는 못 보는 국면 의존을 드러낸다.",
          needs=("layers_daily",), vocab=())
def _stability(lake, *, day: str, etype: str, layer: str = "고유",
               exposure: str = "", **kw) -> dict:
    """전·후 분할 재현 검정.

    **뒤집힘을 못 잡으면 무엇이 거짓으로 납품되는가**: 전 기간 τ 의 부호와 p 만
    실려 나간다. 2022-24년에 +, 2026년에 - 인 관계는 평균이 섞여 작은 +
    하나로 보고되고, 산문은 "이 노출이 높은 종목이 사건 후 초과수익을 냈다" 를
    **오늘 셀에** 적용한다. 오늘은 부호가 반대인 국면이므로 그 문장은 방향까지
    틀린 예측이 되고, 근거로 인용된 p 는 그 오류를 통계로 보증해준 셈이 된다.
    부호가 갈렸다는 사실 자체가 "이 관계는 국면 조건부다" 라는 참인 문장인데,
    분할하지 않으면 그 참인 문장이 거짓 문장으로 교체된다.

    분할이 **날짜 기준**인 이유: 행 수 균등 분할은 같은 사건일을 두 조각에
    걸치게 만들고, 그러면 `_stratified_p` 의 층(=사건일)이 조각마다 쪼개져
    공통충격 소거가 무너진다. 같은 날은 반드시 같은 조각에 있어야 한다.

    PIT: `_base(day)` 의 as_of 클램프와 `_POINT_PANEL` 의 `trade_date < day` 를
    그대로 쓴다 - 오늘 이후 정보는 두 조각 어디에도 못 들어온다.
    """
    if layer not in LAYER_Y:
        return _no(f"층 {layer!r} 은 어휘 밖 - {sorted(LAYER_Y)} 만 결과변수가 있다")
    fam, _, tr = exposure.partition("/")
    key = (fam, tr)
    col = FEATURES.get(key)
    if col is None:
        return _no(f"노출 {exposure!r} 은 아직 못 잰다 - FEATURES 에 열이 없다"
                   " (측정 가능 조합 밖)")
    allowed = LAYER_EXPOSURES[layer]
    if allowed is not None and key not in allowed:
        return _no(f"{layer}층 y 는 {exposure} 를 설명할 자격이 없다 - 층별 허용"
                   " 노출(LAYER_EXPOSURES) 밖이다")

    sql = (_base(day) + _POINT_PANEL).format(etype=etype, cmp="<", day=day,
                                             cols=f"g.{col}", y=LAYER_Y[layer],
                                             refine="")
    rows = _panel_rows(lake, sql)
    if len(rows) < 2 * MIN_N:
        return _no(f"전체 n={len(rows)} - 두 조각이 각각 MIN_N={MIN_N} 을 못 채운다"
                   " (분할 전에 이미 얇다)")

    dates = np.array([str(r[1]) for r in rows])     # _panel_rows 가 날짜 오름차순 정렬
    split = str(dates[len(dates) // 2])
    early, late = dates < split, dates >= split
    n_e, n_l = int(early.sum()), int(late.sum())
    if n_e < MIN_N or n_l < MIN_N:
        thin = "전기" if n_e < MIN_N else "후기"
        return _no(f"{thin} 조각 표본 부족 - 중앙일 {split} 기준 전기 n={n_e} · "
                   f"후기 n={n_l}, MIN_N={MIN_N}. 사건일이 한쪽에 몰려 있어 "
                   "기간 분할로는 재현을 못 묻는다",
                   n_early=n_e, n_late=n_l, split_date=split)

    ar = np.array([float(r[2]) for r in rows])
    xv = np.array([float(r[3]) for r in rows])
    eff_e, p_e = _side(ar[early], xv[early], dates[early])
    eff_l, p_l = _side(ar[late], xv[late], dates[late])

    same = (eff_e > 0) == (eff_l > 0)
    stable = "재현" if same else "뒤집힘"
    lo, hi = sorted((abs(eff_e), abs(eff_l)))
    ratio = float(lo / hi) if hi > 0 else 0.0
    if not same:
        note = (f"부호가 갈렸다 - {split} 전 {eff_e:+.4f} · 후 {eff_l:+.4f}. "
                "전 기간 단일 τ 를 오늘에 적용하면 방향까지 틀린다")
    elif ratio < RATIO_WARN:
        note = (f"부호는 같지만 크기가 {1 / ratio:.1f}배 차이다 (비율 {ratio:.2f} < "
                f"{RATIO_WARN:.2f}) - 국면 변동성만으로 설명되는 폭이라 '같은 크기'"
                "라는 주장은 근거가 없다")
    else:
        note = f"두 기간 부호·크기 모두 재현 (비율 {ratio:.2f})"
    if same and min(p_e, p_l) >= ALPHA:
        # 부호 재현은 "효과가 있다" 가 아니다. 두 조각 다 유의하지 않으면 재현된
        # 것은 잡음의 부호일 수 있다 - 이 문장을 빼면 stable="재현" 이 확증으로
        # 읽힌다(라이브 2026-07-27 RESULT_RELEASE: p 0.32·0.25 인데 "재현").
        note += (f" — 단, 두 조각 모두 유의하지 않다 (p {p_e:.3f}·{p_l:.3f} ≥ "
                 f"ALPHA={ALPHA}). 부호 재현이지 효과 확증이 아니다")
    return {"verdict": "계산됨", "reason": "", "stable": stable,
            "n_early": n_e, "n_late": n_l,
            "eff_early": float(eff_e), "eff_late": float(eff_l),
            "p_early": float(p_e), "p_late": float(p_l),
            "ratio": ratio, "split_date": split, "note": note}


__all__ = ["RATIO_WARN"]
