package com.edge.tenantconsole.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.common.apipayload.code.ErrorReasonDto;
import com.edge.common.apipayload.code.status.ErrorStatus;
import com.edge.common.exception.GeneralException;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.HttpMediaTypeNotSupportedException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

/** 예외 → 공통 응답 봉투(ApiResponse) 매핑 — jvm-common 규약의 웹 계층 글루. */
@RestControllerAdvice
public class GlobalExceptionHandler {

	@ExceptionHandler(GeneralException.class)
	public ResponseEntity<ApiResponse<Void>> handle(GeneralException ex) {
		ErrorReasonDto reason = ex.getErrorReasonHttpStatus();
		return ResponseEntity
				.status(reason.getHttpStatus())
				.body(ApiResponse.<Void>onFailure(reason.getCode(), reason.getMessage(), null));
	}

	// 깨진 JSON·비지원 Content-Type 도 클라이언트 오류다 — catch-all(500)로 흘리지 않는다.
	@ExceptionHandler({MissingServletRequestParameterException.class, MethodArgumentTypeMismatchException.class,
			HttpMessageNotReadableException.class, HttpMediaTypeNotSupportedException.class})
	public ResponseEntity<ApiResponse<Void>> handleBadParameter(Exception ex) {
		ErrorReasonDto reason = ErrorStatus._BAD_REQUEST.getReasonHttpStatus();
		return ResponseEntity
				.status(reason.getHttpStatus())
				.body(ApiResponse.<Void>onFailure(reason.getCode(), reason.getMessage(), null));
	}

	@ExceptionHandler(Exception.class)
	public ResponseEntity<ApiResponse<Void>> handleUnexpected(Exception ex) {
		ErrorReasonDto reason = ErrorStatus._INTERNAL_SERVER_ERROR.getReasonHttpStatus();
		return ResponseEntity
				.status(reason.getHttpStatus())
				.body(ApiResponse.<Void>onFailure(reason.getCode(), reason.getMessage(), null));
	}
}
