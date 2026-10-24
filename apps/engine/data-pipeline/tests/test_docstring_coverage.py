"""공개 정의 docstring 커버리지 게이트 (ALPHA-1023 — 감사 라운드 1 완결 장치).

라운드 1(ALPHA-1018·1021·1022·1023)이 공개 정의 누락 161건을 0 으로 만들었다.
이 테스트는 그 상태를 **코드로 유지**한다 — 산문 체크리스트는 라운드가 거듭되면
새므로, 새 공개 함수·클래스가 docstring 없이 착지하면 CI(pytest)가 거부한다.

interrogate 같은 외부 도구 대신 stdlib ast 를 쓴다: 이미 배선된 pytest 스텝을
재사용하고 의존을 더하지 않는다. 검사 범위는 라운드 1 과 동일하다 — 공개
(비 `_`) 정의만, 테스트 코드 제외. docstring 의 **품질·정합**은 이 게이트 밖이다
(리뷰 소관 — 존재 검사는 기계, 정합 판단은 사람/리뷰).

범위는 라운드 1 이 실제로 집행한 그대로 **비 `_` 이름의 정의 전부**다 — 중첩
함수·비공개 클래스의 메서드도 이름이 공개형이면 포함된다(ast.walk). 외부 API 가
아닌 로컬 헬퍼에 docstring 을 물리고 싶지 않으면 `_` 접두 이름을 쓰면 된다.
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parents[1]
_SCAN_ROOTS = (_APP_ROOT / "src" / "data_pipeline", _APP_ROOT / "scripts")


def _missing_docstrings() -> list[str]:
    out: list[str] = []
    for root in _SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                is_def = isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                )
                if is_def and not node.name.startswith("_") and not ast.get_docstring(node):
                    out.append(f"{path.relative_to(_APP_ROOT)}:{node.lineno} {node.name}")
    return out


def test_public_defs_have_docstrings():
    """공개 정의는 docstring 을 가진다 — 누락 0 이 라운드 1 이 확립한 불변식이다.

    실패하면 목록의 정의에 목적·계약 중심 docstring 을 쓰면 된다. 자명한
    한 줄이면 충분하고, 보장 범위(전량·격리 단위·no-op 여부)는 한 단어도
    과장하지 마라 — 과장은 리뷰에서 거짓 서술로 잡힌다.
    """
    missing = _missing_docstrings()
    assert not missing, (
        f"공개 정의 docstring 누락 {len(missing)}건:\n" + "\n".join(missing)
    )
