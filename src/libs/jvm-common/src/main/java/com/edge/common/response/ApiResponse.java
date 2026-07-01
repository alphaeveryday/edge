package com.edge.common.response;

/**
 * 모든 API 응답의 공통 봉투(envelope) — 프레임워크 비의존 순수 record.
 *
 * <p>성공은 {@code data} 만, 실패는 {@code error} 만 채운다(둘 다 채우지 않는다).
 * 봉투의 "모양"만 여기서 공유하고, 예외를 이 모양으로 매핑하는 로직(@RestControllerAdvice
 * 등)은 각 앱이 자기 웹 계층에서 담당한다.
 *
 * <p>제안(first-cut) 형태다 — 팀 합의에 따라 자유롭게 조정. meta(페이지네이션·requestId)
 * 필드가 필요해지면 여기 확장한다.
 */
public record ApiResponse<T>(T data, ApiError error) {

    public static <T> ApiResponse<T> ok(T data) {
        return new ApiResponse<>(data, null);
    }

    public static <T> ApiResponse<T> fail(ApiError error) {
        return new ApiResponse<>(null, error);
    }

    public static <T> ApiResponse<T> fail(String code, String message) {
        return fail(new ApiError(code, message));
    }
}
