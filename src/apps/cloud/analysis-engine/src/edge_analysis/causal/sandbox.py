"""검정 샌드박스 — **모델이 파이썬을 쓰고, 코드가 무엇을 쓸 수 있는지를 정한다.**

실험판(`experiments/storm/src/storm/prove.py`)의 `Ledger`·`_ns`·`observe` 를 클라우드
데이터 표면(`adapters.causal_data.CausalData`)으로 이식한 것이다. 한동안 프로덕션은
샌드박스를 없애고 "모델은 SQL 술어만 선언한다"로 축약했는데, 그러면 **모델이 새 추정량을
만들 수 없다** — 간선마다 무엇을 어떻게 재야 하는지는 간선마다 다르고, 그게 검정
에이전트가 존재하는 이유다. 그래서 되살린다.

되살리면서 실험판보다 **좁힌** 것 넷:

1. **as_of 를 코드가 바인딩한다.** 모델은 `cohort(where)` 만 부르고 시점 절은 못 만진다.
   PIT 를 한 단어 빠뜨리면 미래를 보는데 그건 사후에 탐지되지 않는다 - 결과가 그냥 좋아진다.
2. **`__` 를 소스에서 금지한다.** 제한 네임스페이스의 고전적 탈출은 전부 던더를 지난다
   (`().__class__.__bases__[0].__subclasses__()`). 여기서는 특히 도구가 바운드 메서드라
   `cohort.__self__._conn` 으로 **원본 DB 커넥션에 닿을 수 있다** - 그 경로를 막는다.
3. **import 는 계산 모듈만.** os·sys·subprocess·socket·pathlib 는 열지 않는다.
4. **쓰기 SQL 은 표면에 없다.** 술어는 `CausalData._guard` 가 검사하고, 노출한 도구는
   전부 SELECT 다.

> 그래도 이것은 **보안 경계가 아니다.** CPython 의 제한 exec 는 완전한 격리가 아니고,
> 여기서 실행되는 코드는 LLM 이 쓴 것이며 그 입력에는 외부 사건 제목이 섞인다(프롬프트
> 주입 표면). 태스크는 최소권한 역할·읽기 전용 DB 사용자로 돌려야 하고, ops 는
> `CAUSAL_SANDBOX_ENABLED=false` 로 이 경로를 끌 수 있어야 한다.
"""
from __future__ import annotations

import builtins
import ctypes
import datetime as dt
import json
import threading
import traceback
from datetime import date
from typing import Any

import numpy as np

from . import stats as S

EXEC_TIMEOUT = 120
MAX_CODE_CHARS = 8000

# 계산에 필요한 것만 연다. numpy 는 내부 모듈을 지연 로드하므로 접두사로 허용해야 한다 -
# 전면 차단하면 np.mean 같은 게 조용히 깨진다.
ALLOWED_MODULES = ("numpy", "math", "statistics", "itertools", "functools",
                   "collections", "datetime", "random", "heapq", "bisect", "operator")
SAFE_BUILTINS = (
    "abs all any bool bytes callable chr complex dict divmod enumerate filter float format "
    "frozenset getattr hasattr hash hex id int isinstance issubclass iter len list map max min "
    "next object oct ord pow print range repr reversed round set slice sorted str sum tuple type "
    "zip True False None NotImplemented Ellipsis"
).split()
SAFE_EXC = (ValueError, TypeError, KeyError, IndexError, RuntimeError, ZeroDivisionError,
            Exception, StopIteration, AttributeError, NameError, ArithmeticError,
            OverflowError, FloatingPointError, AssertionError, NotImplementedError)


class Ledger:
    """`placebo`·`permute` 호출을 **전부** 기록한다. 보고된 하나가 아니라 시도 전부가 남는다.

    스펙 쇼핑(격자를 돌려 유리한 칸만 말하는 것)은 원장에서만 드러난다. 그리고 게이트
    G4 가 "모델이 R 에 적은 p 가 원장에 있는가"를 대조하므로, 손으로 쓴 수치는 통과하지
    못한다 - 실험판에서 날조는 전부 이 자리에서 났다(`p=0.37`, placebo 0회 호출).

    `permute` 도 감싸는 이유: 순열이 원장을 지나지 않으면 **선언과 실행의 불일치를 탐지할
    수 없다.** `R['strata']` 에 층 배열을 담아 놓고 `permute(x)` 를 층 없이 부르면 귀무
    분산이 층 효과로 부풀지만(실측 sd 0.0088 vs 0.0077) 그 사실이 어디에도 남지 않는다.
    선언을 검사하는 것과 실행을 검사하는 것은 다른 일이다.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.perms: list[dict] = []
        self.codes: list[str] = []

    def wrap_placebo(self, stat, obs, nulls, **kw) -> dict:
        r = S.placebo(stat, obs, nulls, **kw)
        self.calls.append({"n": len(self.calls) + 1,
                           # 단측/양측은 방향 주장을 세울 자격과 직결된다. 반환에 없으므로
                           # 호출 인자에서 받아 남긴다 - 없으면 placebo 의 기본값(양측).
                           "two_sided": bool(kw.get("two_sided", True)),
                           # **이 p 를 만든 순열이 어느 것인가.** 직전까지 기록된 순열 수를
                           # 남긴다. 이게 없으면 G7b 가 마지막 순열만 보고, 무층화로 재고
                           # 나중에 층화 permute 를 한 번 더 부르는 것으로 통과한다 -
                           # 보고된 p 는 틀린 교환가능성에서 왔는데 감사는 초록이 된다.
                           "perms_at": len(self.perms),
                           **{k: r.get(k) for k in ("testable", "obs", "p", "n_null",
                                                    "null_sd", "null_kind", "reason")}})
        return r

    def wrap_permute(self, x, strata=None, n: int = 1000, seed: int = 0) -> list:
        out = S.permute(x, strata=strata, n=n, seed=seed)
        try:
            nx = len(np.asarray(x).ravel())
        except Exception:  # noqa: BLE001 - 길이를 못 재는 것도 기록 대상이다
            nx = -1
        self.perms.append({
            "n": len(self.perms) + 1, "len_x": nx, "n_null": n,
            "stratified": strata is not None,
            "n_strata": (len({str(v) for v in np.asarray(strata).ravel().tolist()})
                         if strata is not None else 0)})
        return out

    def spec_sensitive(self, alpha: float = 0.05) -> bool:
        """원장의 p 가 α 를 가로지르나. **가로지르면 결론이 사양 선택에 달려 있다.**

        게이트로 죽이지 않는다 - 여러 사양을 시도하는 것은 정직한 탐색이고, 막으면
        모델이 한 번만 재고 끝낸다. 대신 사실을 산출물에 남겨 확신도를 깎는다.
        """
        ps = [c["p"] for c in self.calls if c.get("testable") and c.get("p") is not None]
        return len(ps) > 1 and min(ps) < alpha <= max(ps)


class SandboxError(RuntimeError):
    """샌드박스가 코드를 **실행하기 전에** 거부했다. 실행 실패(관측)와 다르다."""


class _Killed(Exception):
    """시간초과된 샌드박스 스레드를 끝내려고 비동기로 던지는 것."""


def _kill(t: threading.Thread) -> bool:
    """시간초과 스레드를 **실제로 끝낸다.**

    daemon 스레드를 그냥 두면 프로세스가 끝날 때까지 계속 돈다. 모델이 `while True` 를
    한 번 쓰면 그 태스크의 남은 검정 전부가 CPU 를 나눠 쓰게 된다 - 실측으로 테스트
    스위트가 1.6초에서 138초로 늘었다. ECS 태스크에서는 같은 일이 조용히 일어난다.

    `PyThreadState_SetAsyncExc` 는 순수 파이썬 루프에는 잘 먹고 C 확장(numpy) 호출
    중에는 그 호출이 끝난 뒤에 먹는다. 완전한 선점은 아니라서 성공 여부를 돌려준다 -
    못 죽였으면 관측 문자열에 적어 로그에 남는다.
    """
    if not t.is_alive() or t.ident is None:
        return True
    hit = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_ulong(t.ident), ctypes.py_object(_Killed))
    if hit == 0:
        return False
    t.join(3)
    return not t.is_alive()


def _screen(code: str) -> None:
    """실행 전 소스 검사. 통과 못 하면 관측이 아니라 거부다."""
    if len(code) > MAX_CODE_CHARS:
        raise SandboxError(f"코드가 {len(code)}자다 - 상한 {MAX_CODE_CHARS}. 나눠서 실행해라.")
    if "__" in code:
        raise SandboxError(
            "`__` 를 쓸 수 없다. 던더 속성은 제한 네임스페이스를 우회하는 경로다 "
            "(도구의 __self__ 로 DB 커넥션에 닿는 것을 포함). 도구만 써라.")


def tools(cd, *, as_of: str, w0: date, w1: date, trade_date: date,
          etf_instrument_id: str = "",
          led: Ledger | None = None, docs=None) -> tuple[dict[str, Any], Ledger]:
    """검정 도구 표면과 원장. **시점·대상은 코드가 바인딩하고 모델은 설계만 고른다.**

    `cohort` 에 `as_of` 인자가 없는 것이 핵심이다 - 모델은 PIT 를 우회할 문법이 없다.
    창(`w0`·`w1`)은 넓힐 수 있게 열어 둔다(scope=type 이면 타입 전체로 쌓아야 한다).
    다만 `w1` 은 셀 당일로 잘린다 - 미래 관측을 표본에 넣을 수 없다.
    """
    led = led or Ledger()

    def _clip(a: date | str | None, b: date | str | None) -> tuple[date, date]:
        lo = _as_date(a, w0)
        hi = min(_as_date(b, w1), trade_date)
        return lo, hi

    def cohort(where: str, w0=None, w1=None) -> list[tuple]:
        lo, hi = _clip(w0, w1)
        return cd.cohort(where, as_of=as_of, w0=lo, w1=hi)

    def universe(where: str, dates, exclude=None) -> list[tuple]:
        return cd.universe(where, dates, exclude=exclude)

    def weight(units=None) -> dict:
        if not etf_instrument_id:
            return {"share": None, "n_hold": 0, "reason": "이 셀에 ETF instrument_id 가 없다"}
        return cd.weight(etf_instrument_id, trade_date, units)

    def read(query: str, domain: str | None = None, k: int = 4) -> list[dict]:
        """도메인 문서 조회. **코호트를 짜려면 산업 구조를 알아야 한다.**

        수치 도구가 아니라 산문 도구다 - "상대가 내 하청인가"를 술어로 쓰려면 그 회사의
        공급사 구성을 먼저 읽어야 하고, 그 지식은 표에 없다. 반환은 원문 청크 + 출처이며,
        본문을 자른다(한 번에 4천자 이상 들어오면 남은 턴을 다 먹는다).
        """
        if docs is None:
            return [{"error": "도메인 문서 저장소가 이 셀에 붙어 있지 않다"}]
        try:
            hits = docs.search(query, domain=domain, k=max(1, min(k, 6)))
        except Exception as exc:  # noqa: BLE001 — 조회 실패는 검정 실패가 아니다
            return [{"error": f"{type(exc).__name__}: {exc}"}]
        return [{"domain": h["domain"], "ticker": h["ticker"], "ord": h["ord"],
                 "text": h["text"][:1000]} for h in hits]

    return {
        "np": np, "dt": dt,
        "TRADE_DATE": trade_date, "W0": w0, "W1": w1, "ETF": etf_instrument_id,
        "cohort": cohort, "universe": universe, "weight": weight, "docs": read,
        "ar": cd.ar, "mom": cd.mom, "vol": cd.vol, "prior": cd.prior,
        "permute": led.wrap_permute, "placebo": led.wrap_placebo,
        "fit": S.fit, "predict": S.predict, "residualize": S.residualize,
    }, led


def _as_date(v, default: date) -> date:
    if v is None:
        return default
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def namespace(tool_map: dict) -> dict:
    """제한 네임스페이스. 도구 원본을 `_TOOLS` 에 숨겨 두고 턴마다 복원한다."""
    def _imp(name, *a, **k):
        if name.split(".")[0] in ALLOWED_MODULES:
            return builtins.__import__(name, *a, **k)
        raise ImportError(f"'{name}' 는 막혀 있다. 열린 것: {', '.join(ALLOWED_MODULES)}. "
                          "np(numpy)·dt(datetime) 는 이미 이름으로 있다.")

    safe = {k: getattr(builtins, k) for k in SAFE_BUILTINS if hasattr(builtins, k)}
    safe.update({e.__name__: e for e in SAFE_EXC})
    safe["__import__"] = _imp
    return {"__builtins__": safe, "_TOOLS": dict(tool_map), **tool_map}


def observe(code: str, ns: dict) -> str:
    """코드를 실행한다. **실패도 관측이다** - 모델이 고쳐 쓴다. 상태는 턴 사이 유지된다.

    턴마다 도구 바인딩을 복원한다: 모델이 `ar = [...]` 처럼 도구명을 변수로 덮으면 그
    도구가 영구히 죽는데, 실험판에서 그렇게 6턴을 통째로 날린 적이 있다. 모델 잘못이
    아니다 - 되돌릴 방법이 없었던 것이다.
    """
    try:
        _screen(code)
    except SandboxError as exc:
        return f"거부: {exc}"

    box: dict = {}
    buf: list[str] = []
    ns.update(ns.get("_TOOLS") or {})
    # 샌드박스 전용 print - 전역 stdout 을 건드리지 않는다(병렬 검정에서 섞임 방지)
    ns["__builtins__"] = {**ns["__builtins__"],
                          "print": lambda *a, **k: buf.append(
                              (k.get("sep", " ")).join(str(x) for x in a))}

    def run() -> None:
        try:
            exec(code, ns)  # noqa: S102 - 제한 네임스페이스. 원장이 코드를 전부 보존한다
            box["out"] = "\n".join(buf)
        except _Killed:
            box["out"] = "\n".join(buf) + "\n(시간초과로 중단됨)"
        except Exception:
            box["out"] = "\n".join(buf) + "\n" + traceback.format_exc(limit=2)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(EXEC_TIMEOUT)
    if t.is_alive():
        stopped = _kill(t)
        return (f"시간초과 {EXEC_TIMEOUT}s - 위약 표본(n)을 줄이거나 창을 좁혀라. "
                "코호트를 매 순열마다 다시 조회하고 있지 않은지 봐라."
                + ("" if stopped else
                   " (경고: 중단시키지 못했다 - 계산이 C 확장 안에 있다)"))
    out = (box.get("out") or "").strip()
    if isinstance(ns.get("R"), dict):
        # R 에코는 **절대 실행을 죽이면 안 된다.** date·ndarray 가 들어오면 json 이 터지고,
        # 그게 try 밖이면 관측 전체를 날린다 - 실험판에서 실측으로 걸렸다.
        try:
            out += "\n[R] " + json.dumps(
                {k: (round(v, 6) if isinstance(v, float) else v) for k, v in ns["R"].items()},
                ensure_ascii=False, default=_short)[:1200]
        except Exception as exc:  # noqa: BLE001 - 에코 실패는 관측을 막지 않는다
            out += f"\n[R] 키 {sorted(ns['R'])} (직렬화 불가: {type(exc).__name__})"
    return (out or "(출력 없음 - print 하거나 R 에 placebo 결과를 담아라)")[:2500]


def _short(o) -> str:
    """배열·날짜를 요약해 R 에코가 프롬프트를 잡아먹지 않게 한다."""
    if isinstance(o, np.ndarray):
        return f"<array n={o.size}>"
    if isinstance(o, (list, tuple)):
        return f"<{type(o).__name__} n={len(o)}>"
    return str(o)
