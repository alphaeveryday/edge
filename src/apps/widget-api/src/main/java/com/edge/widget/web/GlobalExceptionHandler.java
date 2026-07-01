package com.edge.widget.web;

import com.edge.common.apipayload.ApiResponse;
import com.edge.common.apipayload.code.ErrorReasonDto;
import com.edge.common.apipayload.code.status.ErrorStatus;
import com.edge.common.exception.GeneralException;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * 전역 예외 → 공통 응답 봉투(ApiResponse) 매핑 예시.
 *
 * <p>역할 분담: 봉투·에러 코드 규약(apipayload)·예외 베이스는 jvm-common 에서, "예외를 HTTP 응답으로
 * 매핑하는 Spring MVC 글루"는 이렇게 각 앱 웹 계층에서 담당한다.
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    /** 예상된 도메인 실패 → BaseErrorCode 의 code + httpStatus 로 매핑. */
    @ExceptionHandler(GeneralException.class)
    public ResponseEntity<ApiResponse<Void>> handle(GeneralException ex) {
        ErrorReasonDto reason = ex.getErrorReasonHttpStatus();
        return ResponseEntity
                .status(reason.getHttpStatus())
                .body(ApiResponse.<Void>onFailure(reason.getCode(), reason.getMessage(), null));
    }

    /** 예상 못한 실패 → 공통 500. 내부 메시지/스택은 클라이언트에 노출하지 않는다. */
    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiResponse<Void>> handleUnexpected(Exception ex) {
        ErrorReasonDto reason = ErrorStatus._INTERNAL_SERVER_ERROR.getReasonHttpStatus();
        return ResponseEntity
                .status(reason.getHttpStatus())
                .body(ApiResponse.<Void>onFailure(reason.getCode(), reason.getMessage(), null));
    }
}
