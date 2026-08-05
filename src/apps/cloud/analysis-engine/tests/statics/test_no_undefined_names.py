"""정의되지 않은 이름을 정적으로 잡는다 — 오늘 하루에 이 부류가 **두 번** 샜다.

  1. `recent_window(shares, labels)` 가 dict 를 리스트로 슬라이스 → `KeyError`
  2. `_dual` 이 다른 함수의 지역변수 `split` 을 참조 → `NameError`

둘 다 367개 테스트를 통과한 상태에서 라이브에서 죽었다. 이유는 같다: 산출 경로
(`run_cell`·`_dual`)가 레이크·LLM 픽스처를 요구해서 실행 테스트가 없고, 있는 것은
`inspect.getsource` 로 **문자열만** 보는 검사였다. 문자열 검사는 이름이 실제로 묶여
있는지 모른다.

`symtable` 은 파이썬 자신의 스코프 해석기다 - 함수 안에서 전역으로 해석된 이름이
모듈 전역에도 빌트인에도 없으면 그건 실행 시 `NameError` 다. 픽스처 없이, 모든
함수에 대해, 한 번에 검사한다.

한계(정직하게): 조건부 정의(`if x: y = 1` 뒤의 `y`)나 `globals()` 조작은 못 본다.
그건 이 검사가 아니라 실행 테스트의 몫이다.
"""

from __future__ import annotations

import builtins
import symtable
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "src" / "edge_analysis"
# 모듈 던더는 인터프리터가 항상 넣어준다 - 대입이 없어도 존재한다.
_ALWAYS = frozenset(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__"}


def _module_globals(tab: symtable.SymbolTable) -> set[str]:
    """모듈 최상단에 묶이는 이름 전량 (import·대입·def·class)."""
    return {s.get_name() for s in tab.get_symbols()
            if s.is_assigned() or s.is_imported() or s.is_namespace()}


def _undefined(tab: symtable.SymbolTable, mod_names: set[str],
               path: str) -> list[str]:
    """함수 스코프를 훑어 해석 안 되는 전역 참조를 모은다."""
    bad: list[str] = []
    for child in tab.get_children():
        if child.get_type() == "function":
            for sym in child.get_symbols():
                name = sym.get_name()
                # `is_global()` = 이 스코프에서 전역으로 해석됐다. 지역·자유변수·
                # 매개변수는 여기 안 걸린다(파이썬이 직접 판정한 것이다).
                if (sym.is_global() and not sym.is_assigned()
                        and name not in mod_names and name not in _ALWAYS):
                    bad.append(f"{path}:{child.get_name()} → {name}")
        bad += _undefined(child, mod_names, path)
    return bad


def test_no_function_references_an_undefined_global():
    """모든 모듈의 모든 함수에서 해석되지 않는 이름이 없다.

    실측으로 잡힌 것: `etfcell._dual` 이 `run` 의 지역 `split` 을 참조했다. 인자로
    넘기지 않았으므로 호출 즉시 `NameError` 이고, 그 경로는 레이크가 필요해서
    실행 테스트가 없었다 - 라이브 4셀이 전부 같은 줄에서 죽고 나서야 드러났다.
    """
    hits: list[str] = []
    for f in sorted(PKG.rglob("*.py")):
        src = f.read_text(encoding="utf-8")
        tab = symtable.symtable(src, str(f), "exec")
        hits += _undefined(tab, _module_globals(tab), f.name)
    assert not hits, "정의되지 않은 이름:\n  " + "\n  ".join(hits)


def test_the_check_actually_catches_the_bug_it_was_written_for():
    """검사기 자신이 그 버그를 잡는지 확인한다 - 안 잡으면 초록이 거짓이다."""
    bug = ("def run():\n"
           "    split = 1\n"
           "    return _dual()\n"
           "\n"
           "def _dual():\n"
           "    if split is not None:\n"
           "        return 1\n"
           "    return 0\n")
    tab = symtable.symtable(bug, "t.py", "exec")
    assert any("split" in h for h in _undefined(tab, _module_globals(tab), "t.py"))

    ok = ("def run():\n"
          "    split = 1\n"
          "    return _dual(split)\n"
          "\n"
          "def _dual(split=None):\n"
          "    return 1 if split is not None else 0\n")
    tab2 = symtable.symtable(ok, "t.py", "exec")
    assert not _undefined(tab2, _module_globals(tab2), "t.py")
