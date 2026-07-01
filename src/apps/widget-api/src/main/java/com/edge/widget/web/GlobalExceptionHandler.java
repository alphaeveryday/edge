package com.edge.widget.web;

import com.edge.common.response.ApiResponse;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * 전역 예외 → 공통 응답 봉투(ApiResponse) 매핑 예시.
 *
 * <p>역할 분담: "봉투의 모양"은 jvm-common({@code ApiResponse}/{@code ApiError})에서,
 * "예외를 봉투로 만드는 로직"은 이렇게 각 앱 웹 계층에서 담당한다.
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    /** 예상된 도메인 실패 → 정의된 code + status 로 매핑. */
    @ExceptionHandler(ApiException.class)
    public ResponseEntity<ApiResponse<Void>> handle(ApiException ex) {
        return ResponseEntity
                .status(ex.getStatus())
                .body(ApiResponse.fail(ex.getCode(), ex.getMessage()));
    }

    /** 예상 못한 실패 → 500. 내부 메시지/스택은 클라이언트에 노출하지 않는다. */
    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiResponse<Void>> handleUnexpected(Exception ex) {
        return ResponseEntity
                .status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(ApiResponse.fail("INTERNAL_ERROR", "예상치 못한 오류가 발생했습니다."));
    }
}
