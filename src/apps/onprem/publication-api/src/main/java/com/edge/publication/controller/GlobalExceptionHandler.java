package com.edge.publication.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.common.apipayload.code.ErrorReasonDto;
import com.edge.common.apipayload.code.status.ErrorStatus;
import com.edge.common.exception.GeneralException;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

/**
 * 예외 → 공통 응답 봉투(ApiResponse) 매핑 — jvm-common 규약의 웹 계층 글루.
 * 봉투는 에러 응답에만 쓴다: 성공(200) 본문은 계약(publication-api.md) 형상 그대로.
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

	/** 파라미터 바인딩 실패 → 400. 클라이언트 오류가 500 으로 위장되지 않게 한다. */
	@ExceptionHandler({MissingServletRequestParameterException.class, MethodArgumentTypeMismatchException.class})
	public ResponseEntity<ApiResponse<Void>> handleBadParameter(Exception ex) {
		ErrorReasonDto reason = ErrorStatus._BAD_REQUEST.getReasonHttpStatus();
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
