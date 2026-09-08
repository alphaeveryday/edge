"""수집 실패를 원문 없이 전달하는 안전한 운영 요약.

공급자 응답·예외 문자열은 토큰, 요청 전문, 계정 식별자를 포함할 수 있다. 원장과
collection_log에는 이 모듈의 고정 어휘만 저장하고 원문은 복사하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _FailureSpec:
    category: str
    summary: str


_SPECS = {
    "KRX_CD010": _FailureSpec("AUTHENTICATION", "KRX 패스워드 변경 필요"),
    "KRX_CD011": _FailureSpec("AUTHENTICATION", "KRX 중복 로그인"),
    "KRX_LOGIN_REJECTED": _FailureSpec("AUTHENTICATION", "KRX 로그인 거부"),
    "KRX_SESSION_REJECTED": _FailureSpec("AUTHENTICATION", "KRX 세션 인증 거부"),
    "HTTP_UNAUTHORIZED": _FailureSpec("AUTHENTICATION", "공급자 인증 거부"),
    "HTTP_FORBIDDEN": _FailureSpec("AUTHENTICATION", "공급자 접근 거부"),
    "RATE_LIMITED": _FailureSpec("TRANSIENT", "공급자 요청 한도 초과"),
    "NETWORK_RETRY_EXHAUSTED": _FailureSpec("TRANSIENT", "네트워크 요청 재시도 소진"),
    "HTTP_REQUEST_REJECTED": _FailureSpec("PROVIDER", "공급자 요청 거부"),
    "PARTIAL_COLLECTION": _FailureSpec("PARTIAL", "일부 대상 수집 실패"),
    "STORAGE_WRITE_FAILED": _FailureSpec("STORAGE", "수집 결과 저장 실패"),
    "COLLECTION_FAILED": _FailureSpec("UNKNOWN", "수집 실패"),
    "UNHANDLED_EXCEPTION": _FailureSpec("UNKNOWN", "처리되지 않은 예외"),
}

_LABELS = {
    "AUTHENTICATION": "인증 실패",
    "TRANSIENT": "일시 장애",
    "PROVIDER": "공급자 오류",
    "PARTIAL": "부분 실패",
    "STORAGE": "저장 실패",
    "UNKNOWN": "원인 미분류",
}


class SafeFailureError(RuntimeError):
    """원문 대신 등록된 안전 요약만 외부로 전달하는 예외."""

    def __init__(self, code: str):
        if code not in _SPECS:
            raise ValueError(f"등록되지 않은 안전 실패 코드: {code}")
        self.code = code
        super().__init__(render_failure(failure_detail(code)))

    def detail(self) -> dict[str, str]:
        """이 예외의 검증된 collection_log 구조를 반환한다."""
        return failure_detail(self.code)


def failure_detail(code: str) -> dict[str, str]:
    """등록된 코드의 직렬화 가능한 구조. 임의 문자열을 받지 않는다."""
    spec = _SPECS[code]
    return {"category": spec.category, "code": code, "summary": spec.summary}


def krx_login_failure(code: object) -> SafeFailureError:
    """KRX 응답 코드를 허용 목록으로 축약한다. 그 밖의 응답 필드는 읽지 않는다."""
    safe_code = f"KRX_{code}" if code in {"CD010", "CD011"} else "KRX_LOGIN_REJECTED"
    return SafeFailureError(safe_code)


def http_failure(status: object) -> dict[str, str]:
    """HTTP 상태만 분류한다. 응답 본문은 운영 로그에 전달하지 않는다."""
    if status == 401:
        return failure_detail("HTTP_UNAUTHORIZED")
    if status == 403:
        return failure_detail("HTTP_FORBIDDEN")
    if status == 429:
        return failure_detail("RATE_LIMITED")
    return failure_detail("HTTP_REQUEST_REJECTED")


def validated_failure(value: object) -> dict[str, str] | None:
    """producer가 낸 구조가 등록된 고정 어휘와 정확히 같은 경우에만 승인한다."""
    if not isinstance(value, dict):
        return None
    code = value.get("code")
    if not isinstance(code, str) or code not in _SPECS:
        return None
    expected = failure_detail(code)
    return expected if value == expected else None


def render_failure(value: object) -> str:
    """검증된 구조를 기존 ops_task_attempt.failure_reason 텍스트로 렌더링한다."""
    detail = validated_failure(value)
    if detail is None:
        return "step_nonzero_exit"
    return f"[{_LABELS[detail['category']]}] {detail['code']} · {detail['summary']}"
