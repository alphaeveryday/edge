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
          "분위", "편차", "가중", "로그", "수익률", "변동성", "지수적", "스레드")
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


def fill_window_template(template: str, values: dict[str, str], *,
                         required: tuple[str, ...]) -> str:
    """모델 문장에서 허용 자리표시자만 받아 코드 값으로 치환한다."""
    slots = re.findall(r"\{([a-z][a-z0-9_]*)\}", template)
    if set(slots) - values.keys():
        raise PlainError("허용되지 않은 자리표시자가 있다")
    if any(slots.count(name) != 1 for name in required):
        raise PlainError("필수 자리표시자는 정확히 한 번 필요하다")
    residue = re.sub(r"\{[a-z][a-z0-9_]*\}", "", template)
    if re.search(r"\d", residue):
        raise PlainError("모델이 숫자나 시각을 직접 썼다")
    if any(value and value in residue for value in values.values()):
        raise PlainError("모델이 종목명이나 출처를 직접 썼다")
    if "{" in residue or "}" in residue:
        raise PlainError("해석할 수 없는 자리표시자가 있다")
    return template.format_map(values)


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


def _win_events(window, labels: dict[str, str] | None) -> list[str]:
    """창에 붙은 사건 id → 사건타입 이름. 맵에 없는 id 는 **버리지 않고 id 로 남긴다**
    (조용히 사라지면 '사건 없는 창' 과 '이름을 못 찾은 창' 이 같아 보인다)."""
    out: list[str] = []
    for eid in getattr(window, "event_ids", ()) or ():
        nm = (labels or {}).get(eid) or str(eid)[:16]
        if nm and nm not in out:
            out.append(nm)
    return out


def recent_window(shares: list, labels: dict[str, str] | None = None,
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
        # `labels` 는 {사건id: 사건타입} 맵이다 - **창의 사건 id 로 조회**한다.
        # 이전 코드는 이걸 리스트로 보고 `labels[i:i+1]` 로 잘랐다: dict 슬라이스는
        # KeyError 라 셀 전체가 죽었다(실측 000660 06-01). 위치로 맞추는 것 자체가
        # 틀렸다 - 창 i 의 사건이 labels 의 i 번째일 이유가 없다.
        return {"when": when, "events": _win_events(w, labels), "kind": w.kind}
    # **빈손으로 돌아가면 시점 강제가 조용히 꺼진다** - 가드가 빈 문자열을 건너뛰기
    # 때문이다(실측 000660 07-27: 창이 많아 전부 floor 미달 -> 최근_시점 ""). 창이
    # 있으면 반드시 하나를 고른다: 몫이 가장 큰 창. 그게 '방금 왜' 의 답이다.
    big = max(shares, key=lambda x: abs(x.log_ret))
    w = big.window
    return {"when": "밤사이" if w.kind == "gap" else when_word(str(w.start)[11:16]),
            "events": _win_events(w, labels), "kind": w.kind}


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
- 주장 블록 세 개에서 다섯 개. 한 블록은 필요한 만큼 여러 문장이어도 된다. 존댓말. 짧게.
- **첫 주장은 '방금/오늘 무엇이 어떻게 됐는지'**, 다음부터 '왜'.
- **재료의 `최근_시점` 낱말을 그 글자 그대로 문장에 넣어라** (예: 재료가 '밤사이' 면
  문장에 '밤사이' 가 들어가야 한다). 하루 요약으로 도망가면 '방금 왜' 에 답이 없다.
  첫 주장에 넣는 것이 가장 자연스럽다: "밤사이부터 오늘 크게 올랐어요".
- 시장을 따라간 것인지, 이 종목만 다르게 간 것인지 **분명히** 말한다.
- 주장마다 근거 참조키를 고른다. 영향이 통계로 확인되고 사건 원문도 있으면
  basis=statistical로 두고 통계(s)와 뉴스(n) 참조를 **함께** 고른다.
- 사건은 **기사 제목**, `arguments`의 **역할별 참여자**, `novelty`·`stage`가 말하는
  **사건 흐름의 위치**를 사실대로 설명한다. `스레드`라는 말은 쓰지 마라. 대신
  "처음 나온 소식", "기존 발표의 후속 조치", "협상 뒤 체결 단계"처럼 풀어라.
- 통계 참조가 영향 방향을 확인하면 "이 사건이 가격을 올렸어요/내렸어요"라고 말해도
  된다. 그러나 "과거에 같은 패턴이 있어서 오늘 이렇게 됐어요"라는 설명은 쓰지 마라.
- 통계 없이 뉴스만 인용하면 basis=narrative다. 사건 사실은 말하되 영향을 주장하지
  말고 sign=0으로 둔다.
- 재료에 `조절 조건` 이 있으면 **어떤 종목에서 더 크게 났는지**를 한 문장으로 말해라.
  `조건` 은 계열족/변환 이름이니 사람 말로 풀어라(예: `주주/수준` -> "외국인 지분이
  많은 종목", `배수/수준` -> "주가가 이미 비싸던 종목"). `방향말` 을 그대로 써라.
  **"원인" 이라고 쓰지 마라** - 이건 조절이다(어떤 종목에서 더 크게 나타났나).
- 주장마다 **방향(sign)** 을 적는다 - 값이 튄 그 시점에 이 이유가 가격을 어느 쪽으로
  밀었나: `1` 올림 · `-1` 내림 · `0` 방향 없음(배경·부재·모른다).
  같은 하루에 +1 과 -1 이 섞이는 것이 정상이다(순풍과 역풍). 억지로 맞추지 마라.
  **첫 주장의 방향은 오늘 하루의 방향과 같아야 한다** - 첫 문장은 오늘 무엇이 어떻게
  됐는지이므로.
- 근거가 아무것도 없으면 "아직 뚜렷한 이유는 안 보여요" 처럼 **모른다고** 말하고
  refs 를 비워라. 지어내면 안 된다.

## 절대 금지
- **숫자를 쓰지 마라.** 아라비아 숫자 하나도 안 된다. 퍼센트, 배수, 날짜 전부.
  재료에 숫자가 있어도 문장에는 옮기지 마라 - 크기는 주어진 등급 낱말로만 말한다.
- 통계·금융 전문용어 금지: 유의·확률·신뢰구간·베타·상관·변동성·수익률·표본.
- 기사 제목·역할별 참여자·사건 흐름을 재료와 다르게 바꾸거나 만들지 마라.
- 재료에 없는 회사명·사건명·지수명을 만들지 마라.
- 사라거나 팔라고 하지 마라. 앞날을 예측하지 마라.
- 묶음 id 를 지어내지 마라 - 너는 참조키만 고른다.

## 재료
{ctx}

## 통계 재료 (basis=statistical 의 근거)
{stats}

## 사건 원문 (기사 제목·역할별 참여자·사건 흐름. 통계 주장에도 함께 참조 가능)
{news}

## 답 (JSON 만)
{{"claims": [{{"text": "한 문장", "basis": "statistical|narrative|none",
              "refs": ["s1"], "sign": 1}}]}}"""


# 방향 낱말. **반대 방향을 말하면 그 산문은 거짓이다** - 실측(091160 07-27): 하루가
# +3.03%p 인데 산문이 '국내 반도체 ETF도 따라 내렸어요' 라고 썼다. 밤사이 해외가
# 내린 것은 사실이라 하락 어휘 자체는 정당하고, 틀린 것은 **주체**다. 그래서 첫 주장이
# 하루의 방향을 반드시 말하게 강제한다(프롬프트도 그것을 요구하고 있었다).
UP, DOWN = ("올랐", "상승", "뛰었", "올라"), ("내렸", "하락", "빠졌", "떨어")


def _dir_guard(first: str, ctx: dict) -> None:
    """첫 주장은 **하루의 방향**을 말해야 한다. 밤사이 이야기로 시작하면 주체가 바뀐다."""
    want = ctx.get("방향") or ""
    if not want:
        return
    if "거의" in want:                       # 무변동은 방향 낱말이 없다
        return
    words = UP if "올랐" in want else DOWN
    if not any(w in first for w in words):
        opp = DOWN if words is UP else UP
        hit = [w for w in opp if w in first]
        raise PlainError(
            f"첫 주장이 하루 방향({want})을 말하지 않았다"
            + (f" - 오히려 반대 낱말 {hit} 을 썼다" if hit else "")
            + f": {first[:44]!r}. 첫 문장은 '오늘 무엇이 어떻게 됐는지' 다")


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
                  layer: str = "", retries: int = 2,
                  allow_narrative: bool = True) -> tuple[str, list]:
    """토스식 산문 + 근거 묶음. 반환 (꼬리표 붙은 산문, Bundle 목록).

    계약 위반이면 **사유를 붙여 다시 묻는다**(감사 2R: 같은 프롬프트 재시도는
    결정론적으로 같은 답을 낸다 - 요청을 바꿔야 한다).

    꼬리표는 코드가 붙인다. 모델은 참조키만 고르고 **묶음 id 를 만들지 못한다** -
    id 생성을 모델에 맡기면 접지가 무너진다(프로젝트 규약).
    """
    import json
    from .model_contract import ask_checked

    news = news or []
    stats = stats or []
    byref = {o["ref"]: o for o in news} | {o["ref"]: o for o in stats}
    sysmsg = _SYSTEM.format(
        ctx=json.dumps(ctx, ensure_ascii=False, indent=1),
        stats=json.dumps(stats, ensure_ascii=False, indent=1) or "(없음)",
        news=json.dumps(news, ensure_ascii=False, indent=1) or "(없음)")
    user, last, why = "", "", ""
    for _ in range(retries + 1):
        out = ask_checked(ask, sysmsg, user or "위 재료로 써라.")
        claims = (out or {}).get("claims") or []
        last = " ".join(str(c.get("text", "")) for c in claims if isinstance(c, dict))
        try:
            return _assemble(claims, ctx, byref, news, cell, day, layer,
                             allow_narrative=allow_narrative)
        except PlainError as e:
            why = str(e)
            user = f"직전 답이 계약을 위반했다: {e}\n고쳐서 다시 써라."
    # **마지막 위반 사유를 반드시 싣는다.** 답만 보여주면 무엇이 계약을 깼는지 알 수
    # 없어 진단이 추측이 된다 - 라이브 5회를 그렇게 낭비했다.
    raise PlainError(f"{why} | 마지막 답: {last[:90]!r}")


# 재료 하나가 **부호 있는 크기 하나**로 요약되는 종류. 여러 개를 실은 재료는 어느 것을
# 말하는지 알 수 없으므로 구속하지 않는다.
#
# `그_창_괴리몫` 을 여기 넣었다가 30일 배치에서 **5일을 죽였다**(8건 실패 중 5건).
# 5분 괴리 분해 재료는 하루·바스켓몫·괴리변화몫·그_창_괴리몫 넷을 싣는데, 주장이
# 말하는 것은 보통 **하루 방향**이고 최대 괴리 창의 부호는 그와 반대일 수 있다
# (실측: 하루 +9.24%p 인데 그_창_괴리몫 -0.00483). 규칙을 내가 세우고 내가 깼다.
_SIGNED_KEY = ("att", "factor_ret", "조절계수")


def _sign_guard(i: int, sign: int, srcs: list[dict]) -> None:
    """주장의 방향이 **인용한 근거와 같은지** 코드가 판정한다.

    `sign` 이 꼬리표와 DB 에만 들어가면 그건 장식이다 - 읽는 자리가 여기다. 두 가지를
    막는다:

      1. **못 가름 근거로 방향을 주장**하는 것. 무유의는 '영향 없음'이 아니지만 방향을
         주장할 근거도 아니다 (실측: p=0.232 · ATT -2.5%p 를 단정으로 읽었다).
      2. **근거와 반대 방향**을 주장하는 것. ATT 가 음수인데 '올렸다(+1)' 면 그 주장은
         자기 근거가 반증한다.
    """
    for st in srcs:
        if any(w in str(st.get("판정", "")) for w in ("못 가름", "미검정")):
            if sign != 0:
                raise PlainError(
                    f"#{i} 못 가름 근거로 방향({sign:+d})을 주장했다 - 무유의는 "
                    "'영향 없음'도 아니지만 방향의 근거도 아니다 (0 이어야 한다)")
            continue
        for k in _SIGNED_KEY:
            v = st.get(k)
            if v is None:
                continue
            want = 1 if float(v) > 0 else -1 if float(v) < 0 else 0
            if sign != want:
                raise PlainError(
                    f"#{i} 방향({sign:+d})이 인용한 근거 {k}={v} 의 부호"
                    f"({want:+d})와 다르다 - 주장이 자기 근거에 반증된다")
            break


# 인용부호 3종. 모델이 원문을 옮겨 적었다고 **주장하는** 구간을 찾는다.
_QUOTE = re.compile(r"[\"“‘「『']([^\"”’」』']{4,})[\"”’」』']")


def _norm(t: str) -> str:
    """공백·문장부호를 지운 비교용 문자열. 띄어쓰기 차이로 진짜 인용이 죽으면 안 된다."""
    return re.sub(r"[\s\u00b7,./·]+", "", t)


def _quote_guard(i: int, txt: str, srcs: list[dict]) -> None:
    """**인용문이 원문에 실제로 있는지** 검사한다 (C10).

    참조 존재만 검사하면 모델이 실재하는 id 를 가리키면서 없는 문장을 지어낼 수 있다 -
    STORM 의 base 가 정확히 그 실패로 죽었다(`EVT_KR_20260601_001` 전량 허구). 그래서
    서사 경로를 켜는 조건이 이 검사였다: 플래그와 같은 커밋에 들어와야 한다.

    검사 대상은 **인용부호로 감싼 구간**뿐이다. 요약·의역은 막지 않는다(막을 방법이
    없고, 막으면 서사가 제목 복사로 퇴화한다). 인용했다고 표시한 것만 책임을 묻는다.
    """
    titles = [_norm(str(s.get("title", ""))) for s in srcs]
    for q in _QUOTE.findall(txt):
        if not any(_norm(q) in t for t in titles):
            raise PlainError(
                f"#{i} **지어낸 인용**: {q[:40]!r} 가 인용한 기사 제목에 없다 - "
                f"제목: {[str(s.get('title', ''))[:30] for s in srcs]}")


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
              cell: str, day: str, layer: str, *,
              allow_narrative: bool = True) -> tuple[str, list]:
    """주장 목록 → (산문, 묶음). 주장 하나가 계약을 깨면 **전체를 되묻는다** -
    한 문장만 버리면 남은 문장이 그 문장에 의존한 채로 나갈 수 있다."""
    from .evidence import news_bundle, stat_bundle
    if not claims:
        raise PlainError("주장이 없다")
    lines: list[str] = []
    bundles: list = []
    # 하루 방향의 부호. 무변동 셀은 첫 주장 방향을 강제하지 않는다.
    d = str(ctx.get("방향") or "")
    want_sign = 1 if "올랐" in d else -1 if "내렸" in d else None
    whole = " ".join(str(c.get("text", "")) for c in claims if isinstance(c, dict))
    guard(whole, ctx)                       # 숫자·용어·접지·최근시점은 전체에서 본다
    _dir_guard(str(claims[0].get("text", "")) if isinstance(claims[0], dict) else "", ctx)
    for i, c in enumerate(claims, 1):
        if not isinstance(c, dict) or not str(c.get("text", "")).strip():
            raise PlainError(f"#{i} 주장이 비었다")
        txt = str(c["text"]).strip()
        basis = str(c.get("basis") or "none")
        refs = [str(r) for r in (c.get("refs") or [])]
        # 방향은 **셋 중 하나**다. 빠뜨리거나 다른 값을 주면 즉사 - 기계가 읽는 칸에
        # 모델의 자유서술이 들어오면 집계가 조용히 틀린다.
        raw_sign = c.get("sign", None)
        try:
            sign = int(raw_sign)
        except (TypeError, ValueError):
            raise PlainError(f"#{i} 방향(sign)이 없거나 수가 아니다: {raw_sign!r} "
                             "- 1(올림)·0(무관)·-1(내림) 중 하나를 적어라") from None
        if sign not in (-1, 0, 1):
            raise PlainError(f"#{i} 방향은 1·0·-1 중 하나다: {sign}")
        if i == 1 and want_sign is not None and sign != want_sign:
            raise PlainError(
                f"#1 첫 주장의 방향({sign:+d})이 하루 방향({want_sign:+d})과 다르다 "
                "- 첫 문장은 '오늘 무엇이 어떻게 됐는지' 다")
        if basis == "none" or not refs:
            # **첫 주장은 설명 대상(하루 방향)을 말한다 - 근거를 요구하지 않는다.**
            # 하루 수익률은 이 셀이 존재하는 이유이고 가격 계열에 접지돼 있다. 이걸
            # '근거 없는 주장' 으로 잡으면 두 가드가 서로 모순한다: 첫 주장은 방향을
            # 말해야 하고(위) 동시에 모른다고 말해야 한다. 실측(000660 06-01): 모델이
            # 방향과 '아직 이유는 안 보여요' 를 두 문장으로 정확히 나눴는데 #1 에서
            # 죽었다. 이유 주장(i≥2)은 여전히 근거나 '모른다' 를 요구한다.
            first_states_day = (i == 1 and want_sign is not None
                                and any(w in txt for w in ("올랐", "내렸", "상승", "하락")))
            if not first_states_day and not any(
                    w in txt for w in ("안 보여", "알 수 없", "뚜렷하지", "찾지 못",
                                       "확인되지", "모르", "아직")):
                raise PlainError(f"#{i} 근거 없는 주장인데 모른다고 말하지 않았다: "
                                 f"{txt[:40]!r}")
            # 조회할 묶음이 없으므로 근거 부재만 표시한다. 방향은 문장 자체가 말한다.
            lines.append(f"{txt} {{none, -}}")
            continue
        if bad := [r for r in refs if r not in byref]:
            raise PlainError(f"#{i} 없는 참조 {bad} - 재료에 없다 (날조 폐기)")
        stat_refs = [r for r in refs if r.startswith("s")]
        news_refs = [r for r in refs if not r.startswith("s")]
        if basis == "statistical":
            if not stat_refs:
                raise PlainError(f"#{i} statistical 주장인데 통계 참조가 없다")
            _stat_guard(i, txt, [byref[r] for r in stat_refs])
            _sign_guard(i, sign, [byref[r] for r in stat_refs])
            if news_refs:
                _quote_guard(i, txt, [byref[r] for r in news_refs])
            st = {}
            for r in stat_refs:
                st.update({k: v for k, v in byref[r].items()
                           if k not in ("ref", "claim")})
            b = stat_bundle(cell, day, txt, layer=layer, sign=sign,
                            news=news, refs=news_refs, **st)
        elif basis == "narrative":
            if not allow_narrative:
                raise PlainError(f"#{i} 서사 경로가 비활성이다")
            if stat_refs or not news_refs:
                raise PlainError(f"#{i} narrative 주장에는 뉴스 참조만 허용한다")
            _quote_guard(i, txt, [byref[r] for r in news_refs])
            b = news_bundle(cell, day, txt, news, news_refs, layer=layer, sign=sign)
        else:
            raise PlainError(f"#{i} 알 수 없는 basis={basis!r}")
        bundles.append(b)
        lines.append(f"{txt} {b.tag}")
    return " ".join(lines), bundles


CARD_MAX = 45           # MTS 목록 카드 한 줄 상한(자). 넘으면 잘리고 뜻이 바뀐다


def card(plain: str, *, limit: int = CARD_MAX) -> str:
    """쉬운 설명 → **목록 카드 한 줄**. 첫 문장이 카드다.

    세 길이를 쓴다: 카드 한 줄(목록에서 스캔) · 쉬운 설명 3~5문장(카드를 열었을 때) ·
    정직한 전문(근거 보기). 지금까지 원장의 `headline` 에 **쉬운 설명 전문**이 들어가
    카드가 네 문장이었다 - 목록에서 잘리면 뜻이 바뀐다.

    카드를 따로 쓰지 않고 **첫 문장을 그대로 쓴다**: 가드가 이미 첫 문장에 '오늘 무엇이
    어떻게 됐는지' 를 강제하므로 카드에 필요한 것이 거기 있다. 따로 생성하면 (a) 새 LLM
    호출과 새 실패 지점이 생기고 (b) 카드와 본문이 어긋날 수 있다.
    """
    body = plain.strip().lstrip("=").strip()
    first = re.split(r"(?<=[.!?요])\s", body, maxsplit=1)[0] if body else ""
    first = re.sub(r"\s*\{[^}]*\}\s*", " ", first).strip()   # 꼬리표는 카드에 안 싣는다
    if not first:
        return ""
    if len(first) <= limit:
        return first
    # 낱말 경계에서 자른다 - 글자 중간에서 끊으면 뜻이 바뀐다
    cut = first[:limit]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp >= limit // 2 else cut).rstrip(" ,·") + "…"


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
