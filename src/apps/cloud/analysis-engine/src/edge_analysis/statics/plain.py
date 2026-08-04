"""토스식 설명 — 일반 투자자용. **수치 없이** 왜 방금 움직였는지 말한다.

## 왜 둘로 나누나

정직한 설명(`narrate`·`etfcell` 산문)은 통계량·검정한 가설·구간을 다 싣는다. 그것이
SSOT 다. 그런데 사람이 이 화면을 보는 순간은 **가격이 급변했을 때**와 **시장과 다르게
움직일 때**다. 그때 필요한 답은 'p=0.004' 가 아니라 "지금 반도체가 밤새 미국에서
올라서 우리 시장이 통째로 뛰었고, 이 종목만의 이야기는 아니다" 다.

두 산문은 **같은 사실**에서 나온다. 토스식은 정직한 설명이 이미 확정한 것만 옮긴다 -
새로 주장하지 않는다. 그래서 이 모듈은 가설도 검정도 하지 않는다.

## 코드가 강제하는 것 (모델의 재량이 아니다)

  · **아라비아 숫자 금지.** 하나라도 있으면 즉사. '수치가 있으면 안 된다' 는 요구를
    프롬프트로 부탁하면 지켜지지 않는다 - 실측에서 모델은 늘 숫자를 흘린다.
  · **통계 용어 금지.** p값·유의·신뢰구간·ATT·베타·상관 - 토스식이 아니다.
  · **접지.** 사건 이름·종목 이름은 확정된 것만. 목록 밖 고유명사는 즉사.
  · **크기·확신은 등급으로.** 등급은 코드가 매긴다(임계 고정). 모델이 '급등' 을
    고를 수 없다 - 그러면 같은 하루가 날마다 다른 강도로 불린다.
  · **최근 창을 반드시 말한다.** 하루 전체 요약으로 도망가면 '방금 왜' 에 답이 없다.

## 등급 (임계는 전역 상수 - 가설별 지정 금지)
    조금 · 뚜렷하게 · 크게 · 아주 크게        일간 |로그수익|
    시장 따라 · 시장보다 더 · 시장과 반대로    고유/총 부호와 몫
"""
from __future__ import annotations

import re

# 크기 등급 임계 (일간 |로그수익|, 비율). 고정 - 셀마다 바꾸면 같은 하루가 달리 불린다.
SIZE_STEPS = ((0.010, "조금"), (0.030, "뚜렷하게"), (0.070, "크게"))
SIZE_TOP = "아주 크게"
# 고유 몫이 이 미만이면 '시장 따라' 다 (라우팅 DOMINANT 와 같은 정신, 산문용 임계)
IDIO_SHARE = 0.35

BANNED = ("p값", "p-value", "유의", "신뢰구간", "구간", "표본", "베타", "상관",
          "회귀", "검정", "통계", "확률", "퍼센트", "포인트", "%", "ATT", "CATE",
          "분위", "편차", "가중", "로그", "수익률", "변동성", "지수적")
_DIGIT = re.compile(r"[0-9０-９]")

# **무유의 ≠ 영향 없음.** 표본이 얇아 못 가른 것을 '영향 없음' 으로 바꾸는 것은
# 부재를 기각으로 위장하는 짓이다(설계 §11). 실측: EXPORT_CONTROL 이 ATT -2.5%p ·
# p=0.232 · 처치일 10 인데 산문이 '큰 영향을 주지 않았어요' 라고 단정했다.
NEG_ASSERT = ("영향을 주지 않았", "영향이 없", "영향은 없", "때문이 아니",
              "관계가 없", "상관없", "무관")
# 근거 자체가 결손이면(β 미계측 등) 단정 어휘를 쓸 수 없다 - 구간은 살아 있어도
# 점 인과를 말할 수는 없다.
STRONG_ASSERT = ("때문이에요", "때문입니다", "영향을 받았어요", "덕분", "탓")
WEAK_MARK = ("미계측", "백필 필요", "판정불가", "부족")


class PlainError(ValueError):
    """토스식 계약 위반. 고치기 전에 절대 내보내지 않는다."""


def size_word(log_ret: float) -> str:
    """크기 → 말. **코드가 정한다** - 모델이 '급등' 을 고르면 강도가 날마다 흔들린다."""
    a = abs(log_ret)
    for cut, w in SIZE_STEPS:
        if a < cut:
            return w
    return SIZE_TOP


def relation_word(day_log: float, idio_log: float) -> str:
    """시장과의 관계 → 말. 사람이 이 화면을 여는 두 번째 이유가 이것이다."""
    if abs(day_log) < 1e-9:
        return "시장 따라"
    share = abs(idio_log) / abs(day_log)
    if share < IDIO_SHARE:
        return "시장 따라"
    return "시장과 반대로" if idio_log * day_log < 0 else "시장보다 더"


WHEN_STEPS = ((9, 40, "장 열린 직후"), (11, 30, "오전 중"), (13, 0, "점심 무렵"),
              (14, 40, "오후"), (24, 0, "장 마감 무렵"))


def when_word(hhmm: str) -> str:
    """시각 → 말. 토스식에 '십사시 오십분' 을 쓸 수는 없고 숫자는 금지다.

    창의 **시작** 시각으로 부른다 - 사람이 기억하는 것은 '오후에 빠졌다' 다.
    장 시작 전(갭)은 시각이 없으므로 호출자가 '밤사이' 를 직접 넘긴다.
    """
    try:
        h, m = (int(x) for x in hhmm.split(":")[:2])
    except Exception:                       # noqa: BLE001 - 형식 밖은 빈 말
        return ""
    for hh, mm, w in WHEN_STEPS:
        if (h, m) < (hh, mm):
            return w
    return WHEN_STEPS[-1][2]


def recent_window(shares: list, labels: list[str] | None = None,
                  floor: float = 0.20) -> dict:
    """마지막 **유의미한** 창. 하루 요약으로 도망가지 않게 재료를 고정한다.

    `floor`: 하루 총합 대비 이 비율 미만인 창은 '방금 왜' 의 답이 아니다 - 마지막
    창이 거의 0 이면 그 앞의 실제로 움직인 창을 말해야 한다.
    """
    if not shares:
        return {}
    tot = sum(abs(s.log_ret) for s in shares) or 1.0
    for i in range(len(shares) - 1, -1, -1):
        s = shares[i]
        if abs(s.log_ret) / tot < floor:
            continue
        w = s.window
        when = ("밤사이" if w.kind == "gap"
                else when_word(str(w.start)[11:16]))
        evs = [e for e in ((labels or [])[i:i + 1] if labels else []) if e]
        return {"when": when, "events": evs, "kind": w.kind}
    return {}


def context(*, ticker_name: str, day_log: float, idio_log: float, route_kind: str,
            market_name: str, recent: dict, established: list[str],
            overnight: list[str], unexplained_top: bool,
            idio_qualified: bool = True) -> dict:
    """모델에 넘길 **수치 없는** 재료. 여기서 이미 숫자를 다 지운다.

    `recent`: 최근 창 하나 - {"when": "장 마감 전", "events": [이름...], "kind": ...}
    `established`: 검정을 통과한 채널·사건 이름 (사람 말로)
    `overnight`: 밤사이 해외에서 오른/내린 것의 이름 (수치 없이)
    """
    return {
        "종목": ticker_name,
        "방향": "올랐어요" if day_log > 0 else "내렸어요" if day_log < 0 else "거의 안 움직였어요",
        "크기": size_word(day_log),
        "시장관계": relation_word(day_log, idio_log),
        "끌어당긴_층": {"시장": f"시장 전체({market_name})", "섹터": "같은 업종",
                     "고유": "이 종목만의 사정", "혼합": "여러 갈래",
                     "괴리단독": "이 상품 자체의 수급"}.get(route_kind, route_kind),
        "최근_시점": recent.get("when", ""),
        "최근_사건": list(recent.get("events") or []),
        "확인된_이유": list(established),
        "밤사이_해외": list(overnight),
        "미설명_최대": unexplained_top,
        "고유_신뢰": idio_qualified,
    }


_SYSTEM = """너는 토스 앱의 설명 문구를 쓴다. 읽는 사람은 방금 가격이 튄 걸 보고
'왜?' 를 눌렀다. 투자 지식이 없다고 가정한다.

## 반드시
- 주장 세 개에서 다섯 개. 각 주장은 한 문장. 존댓말. 짧게.
- **첫 주장은 '방금/오늘 무엇이 어떻게 됐는지'**, 다음부터 '왜'.
- **최근 시점의 움직임을 반드시 언급**한다 - 하루 요약으로 도망가지 마라.
- 시장을 따라간 것인지, 이 종목만 다르게 간 것인지 **분명히** 말한다.
- 주장마다 **근거를 고른다**: 참조키 목록. 통계 재료(s로 시작)면 basis=statistical,
  뉴스(n으로 시작)면 basis=narrative. 근거 없는 주장은 내지 마라.
- 근거가 아무것도 없으면 "아직 뚜렷한 이유는 안 보여요" 처럼 **모른다고** 말하고
  refs 를 비워라. 지어내면 안 된다.

## 절대 금지
- **숫자를 쓰지 마라.** 아라비아 숫자 하나도 안 된다. 퍼센트, 배수, 날짜 전부.
  재료에 숫자가 있어도 문장에는 옮기지 마라 - 크기는 주어진 등급 낱말로만 말한다.
- 통계·금융 전문용어 금지: 유의·확률·신뢰구간·베타·상관·변동성·수익률·표본.
- **기사 제목을 옮겨 쓰지 마라.** 무슨 일인지 네 말로 풀고, 출처는 refs 로 가리킨다.
- 재료에 없는 회사명·사건명·지수명을 만들지 마라.
- 사라거나 팔라고 하지 마라. 앞날을 예측하지 마라.
- 묶음 id 를 지어내지 마라 - 너는 참조키만 고른다.

## 재료
{ctx}

## 통계 재료 (basis=statistical 의 근거)
{stats}

## 뉴스 (basis=narrative 의 근거 - 제목은 읽되 옮겨 쓰지 마라)
{news}

## 답 (JSON 만)
{{"claims": [{{"text": "한 문장", "basis": "statistical|narrative|none",
              "refs": ["s1"]}}]}}"""


def guard(text: str, ctx: dict) -> str:
    """계약 검사. 위반은 **즉사** - 고쳐 보내지 않는다(고치면 무엇이 계약인지 흐려진다)."""
    if not text or not text.strip():
        raise PlainError("빈 산문")
    if m := _DIGIT.search(text):
        raise PlainError(f"숫자가 있다({m.group()!r}) - 토스식은 수치를 쓰지 않는다")
    if bad := [w for w in BANNED if w in text]:
        raise PlainError(f"전문용어 {bad} - 일반 투자자용이 아니다")
    # 접지: 재료에 없는 고유명사(따옴표·괄호로 강조된 이름)를 만들면 즉사
    known = {ctx["종목"], ctx["끌어당긴_층"]} | set(ctx["최근_사건"]) \
        | set(ctx["확인된_이유"]) | set(ctx["밤사이_해외"])
    known = {k for k in known if k}
    for tok in re.findall(r"[‘'\"“]([^’'\"”]{2,20})[’'\"”]", text):
        if not any(tok in k or k in tok for k in known):
            raise PlainError(f"접지 없는 이름 {tok!r} - 재료에 없다")
    if not ctx["최근_사건"] and not ctx["확인된_이유"]:
        # 이유를 모를 때는 모른다고 말해야 한다 - 조용히 그럴듯한 문장을 내면 거짓이다
        if not any(w in text for w in ("안 보여", "알 수 없", "뚜렷하지", "찾지 못",
                                       "확인되지", "모르", "없어요", "아직")):
            raise PlainError("확인된 이유가 없는데 모른다고 말하지 않았다")
    if ctx["최근_시점"] and ctx["최근_시점"] not in text:
        raise PlainError(f"최근 시점({ctx['최근_시점']})을 말하지 않았다 - "
                         "하루 요약으로는 '방금 왜' 에 답이 안 된다")
    return text.strip()


def narrate_plain(ask, ctx: dict, *, news: list[dict] | None = None,
                  stats: list[dict] | None = None, cell: str = "", day: str = "",
                  layer: str = "", retries: int = 2) -> tuple[str, list]:
    """토스식 산문 + 근거 묶음. 반환 (꼬리표 붙은 산문, Bundle 목록).

    계약 위반이면 **사유를 붙여 다시 묻는다**(감사 2R: 같은 프롬프트 재시도는
    결정론적으로 같은 답을 낸다 - 요청을 바꿔야 한다).

    꼬리표는 코드가 붙인다. 모델은 참조키만 고르고 **묶음 id 를 만들지 못한다** -
    id 생성을 모델에 맡기면 접지가 무너진다(프로젝트 규약).
    """
    import json

    from .evidence import news_bundle, stat_bundle
    news = news or []
    stats = stats or []
    byref = {o["ref"]: o for o in news} | {o["ref"]: o for o in stats}
    sysmsg = _SYSTEM.format(
        ctx=json.dumps(ctx, ensure_ascii=False, indent=1),
        stats=json.dumps(stats, ensure_ascii=False, indent=1) or "(없음)",
        news=json.dumps(news, ensure_ascii=False, indent=1) or "(없음)")
    user, last, why = "", "", ""
    for _ in range(retries + 1):
        out = ask(sysmsg, user or "위 재료로 써라.")
        claims = (out or {}).get("claims") or []
        last = " ".join(str(c.get("text", "")) for c in claims if isinstance(c, dict))
        try:
            return _assemble(claims, ctx, byref, news, cell, day, layer)
        except PlainError as e:
            why = str(e)
            user = f"직전 답이 계약을 위반했다: {e}\n고쳐서 다시 써라."
    # **마지막 위반 사유를 반드시 싣는다.** 답만 보여주면 무엇이 계약을 깼는지 알 수
    # 없어 진단이 추측이 된다 - 라이브 5회를 그렇게 낭비했다.
    raise PlainError(f"{why} | 마지막 답: {last[:90]!r}")


def _stat_guard(i: int, txt: str, srcs: list[dict]) -> None:
    """통계 근거의 **강도를 넘는 단정**을 막는다. 등급은 코드가 읽는다."""
    from .vocab import ALPHA
    ps = [float(s["p"]) for s in srcs if s.get("p") is not None]
    insig = bool(ps) and all(p >= ALPHA for p in ps)
    if insig and (hit := [w for w in NEG_ASSERT if w in txt]):
        raise PlainError(
            f"#{i} 무유의(p≥{ALPHA})를 {hit} 로 단정했다 - 못 가른 것과 영향 없는 "
            f"것은 다르다. '뚜렷하지 않아요' 처럼 써라")
    # 결손 표시가 있어도 **0 을 배제하는 구간**이 있으면 단정할 자격이 있다 -
    # 실측 s1 은 ETF 자기 갭 β 가 미계측이지만 코스피 환원 구간 [+1.08, +5.68]%p 가
    # 살아 있다. 식별집합이 0 을 넘지 않는 것이 인과 주장의 면허다.
    def _usable(sc: dict) -> bool:
        iv = sc.get("explained") or sc.get("iset")
        if iv and len(iv) == 2 and all(v is not None for v in iv):
            lo, hi = sorted(float(v) for v in iv)
            if lo > 0 or hi < 0:
                return True
        return sc.get("p") is not None and float(sc["p"]) < ALPHA
    weak = [w for sc in srcs if not _usable(sc)
            for w in WEAK_MARK if w in str(sc.get("note", ""))]
    if weak and (hit := [w for w in STRONG_ASSERT if w in txt]):
        raise PlainError(f"#{i} 근거가 결손({weak})인데 {hit} 로 단정했다 - "
                         f"'~한 영향으로 보여요' 처럼 낮춰라")


def _assemble(claims: list, ctx: dict, byref: dict, news: list[dict],
              cell: str, day: str, layer: str) -> tuple[str, list]:
    """주장 목록 → (산문, 묶음). 주장 하나가 계약을 깨면 **전체를 되묻는다** -
    한 문장만 버리면 남은 문장이 그 문장에 의존한 채로 나갈 수 있다."""
    from .evidence import news_bundle, stat_bundle
    if not claims:
        raise PlainError("주장이 없다")
    lines: list[str] = []
    bundles: list = []
    whole = " ".join(str(c.get("text", "")) for c in claims if isinstance(c, dict))
    guard(whole, ctx)                       # 숫자·용어·접지·최근시점은 전체에서 본다
    for i, c in enumerate(claims, 1):
        if not isinstance(c, dict) or not str(c.get("text", "")).strip():
            raise PlainError(f"#{i} 주장이 비었다")
        txt = str(c["text"]).strip()
        basis = str(c.get("basis") or "none")
        refs = [str(r) for r in (c.get("refs") or [])]
        if basis == "none" or not refs:
            # 근거 없는 주장은 **모른다는 말일 때만** 허용한다
            if not any(w in txt for w in ("안 보여", "알 수 없", "뚜렷하지", "찾지 못",
                                          "확인되지", "모르", "아직")):
                raise PlainError(f"#{i} 근거 없는 주장인데 모른다고 말하지 않았다: "
                                 f"{txt[:40]!r}")
            lines.append(txt)
            continue
        if bad := [r for r in refs if r not in byref]:
            raise PlainError(f"#{i} 없는 참조 {bad} - 재료에 없다 (날조 폐기)")
        kinds = {("statistical" if r.startswith("s") else "narrative") for r in refs}
        if len(kinds) > 1:
            raise PlainError(f"#{i} 통계와 서사 근거를 한 주장에 섞었다 - "
                             "무엇이 근거인지 흐려진다")
        kind = kinds.pop()
        if basis != kind:
            raise PlainError(f"#{i} basis={basis} 인데 참조는 {kind} 다")
        if kind == "statistical":
            _stat_guard(i, txt, [byref[r] for r in refs])
        if kind == "narrative":
            b = news_bundle(cell, day, txt, news, refs, layer=layer)
        else:
            st = {}
            for r in refs:
                st.update({k: v for k, v in byref[r].items() if k != "ref"})
            b = stat_bundle(cell, day, txt, layer=layer, **st)
        bundles.append(b)
        lines.append(f"{txt} {b.tag}")
    return " ".join(lines), bundles


def dual(honest: str, plain: str, bundles: list | None = None) -> str:
    """두 설명을 한 산출물로. **정직한 것이 먼저**다 - 토스식은 그것의 요약이지
    대체가 아니고, 순서를 뒤집으면 근거 없이 읽고 덮는다."""
    from .evidence import say_bundles
    out = (honest.rstrip() + "\n\n"
           + "=" * 60 + "\n[쉬운 설명] 수치 없이 - 방금 왜 움직였나\n"
           + "=" * 60 + "\n" + plain.strip() + "\n")
    if bundles:
        out += "\n" + say_bundles(bundles) + "\n"
    return out


def _selfcheck() -> None:
    assert size_word(0.005) == "조금" and size_word(0.02) == "뚜렷하게"
    assert size_word(0.05) == "크게" and size_word(0.25) == SIZE_TOP
    assert relation_word(0.10, 0.005) == "시장 따라"
    assert relation_word(0.10, 0.08) == "시장보다 더"
    assert relation_word(-0.05, 0.04) == "시장과 반대로"

    ctx = context(ticker_name="KODEX 반도체", day_log=0.2546, idio_log=0.0064,
                  route_kind="시장", market_name="코스피 대형주", recent={
                      "when": "장 마감 무렵", "events": ["미국 반도체 강세"]},
                  established=[], overnight=["미국 반도체"], unexplained_top=False)
    assert ctx["크기"] == SIZE_TOP and ctx["시장관계"] == "시장 따라"

    good = "오늘 KODEX 반도체가 아주 많이 올랐어요. 장 마감 무렵까지 오름세가 이어졌어요. 시장 전체가 함께 올라서 이 상품만의 일은 아니에요."
    assert guard(good, ctx) == good
    for bad, why in (("오늘 반도체가 25% 올랐어요. 장 마감 무렵이요.", "숫자"),
                     ("장 마감 무렵 유의하게 올랐어요.", "전문용어"),
                     ("장 마감 무렵 '엔비디아' 때문이에요.", "접지"),
                     ("오늘 많이 올랐어요. 시장 따라 갔어요.", "최근 시점")):
        try:
            guard(bad, ctx)
        except PlainError:
            continue
        raise AssertionError(f"{why} 위반을 통과시켰다: {bad}")

    # 이유를 모를 때는 모른다고 말해야 한다
    blind = context(ticker_name="A", day_log=0.05, idio_log=0.04, route_kind="고유",
                    market_name="M", recent={"when": "오후"}, established=[],
                    overnight=[], unexplained_top=True)
    try:
        guard("오후에 크게 올랐어요. 이 종목만의 힘이었어요.", blind)
        raise AssertionError("모른다고 말하지 않은 산문을 통과시켰다")
    except PlainError:
        pass
    assert guard("오후에 크게 올랐어요. 뚜렷한 이유는 아직 안 보여요.", blind)

    assert when_word("09:05") == "장 열린 직후" and when_word("13:20") == "오후"
    assert when_word("15:10") == "장 마감 무렵" and when_word("x") == ""

    class _W:
        def __init__(self, kind, start): self.kind, self.start = kind, start
    class _S:
        def __init__(self, w, r): self.window, self.log_ret = w, r
    ss = [_S(_W("gap", "2026-07-31 09:00:00"), 0.03),
          _S(_W("event", "2026-07-31 13:30:00"), 0.001)]
    # 마지막 창이 거의 0 이면 그 앞의 실제로 움직인 창을 말한다
    assert recent_window(ss)["when"] == "밤사이"
    ss[1].log_ret = 0.05
    assert recent_window(ss)["when"] == "오후"

    # 주장 조립: 꼬리표는 코드가 붙이고, 근거 섞기·날조 참조는 즉사
    news = [{"ref": "n1", "news_id": "NEWS_A", "title": "수주", "type": "SIGNING",
             "thread": "t1", "t": "09:10"}]
    stt = [{"ref": "s1", "etype": "CONTRACT.SIGNING", "p": 0.004, "n_pairs": 138}]
    br = {o["ref"]: o for o in news} | {o["ref"]: o for o in stt}
    ok_claims = [{"text": "장 마감 무렵 크게 올랐어요", "basis": "statistical",
                  "refs": ["s1"]},
                 {"text": "새 계약 소식이 있었어요", "basis": "narrative",
                  "refs": ["n1"]}]
    txt, bs = _assemble(ok_claims, ctx, br, news, "091160", "2026-07-31", "고유")
    assert "{statistical, ev_" in txt and "{narrative, ev_" in txt
    assert len(bs) == 2 and bs[0].basis == "statistical"
    for bad, why in (
            ([{"text": "장 마감 무렵 올랐어요", "basis": "narrative", "refs": ["없음"]}], "날조 참조"),
            ([{"text": "장 마감 무렵 올랐어요", "basis": "narrative", "refs": ["s1", "n1"]}], "근거 섞기"),
            ([{"text": "장 마감 무렵 올랐어요", "basis": "statistical", "refs": ["n1"]}], "basis 불일치"),
            ([{"text": "장 마감 무렵 올랐어요", "basis": "none", "refs": []}], "근거 없이 단언")):
        try:
            _assemble(bad, ctx, br, news, "c", "d", "")
        except PlainError:
            continue
        raise AssertionError(f"{why} 를 통과시켰다")

    d = dual("정직한 설명", "쉬운 설명")
    assert d.index("정직한 설명") < d.index("쉬운 설명")
    assert "근거 묶음" in dual("h", "p", bs)
    print("ok")


if __name__ == "__main__":
    _selfcheck()
