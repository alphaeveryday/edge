package com.edge.tenantsync.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.common.apipayload.code.ErrorReasonDto;
import com.edge.common.apipayload.code.status.ErrorStatus;
import com.edge.common.exception.GeneralException;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * 예외 → 공통 응답 봉투(ApiResponse) 매핑 — jvm-common 규약의 웹 계층 글루.
 * 봉투는 에러 응답에만 쓴다: 성공(200) 번들 본문은 계약 와이어 포맷 그대로이며 체크섬 대상이라
 * 봉투로 감싸지 않는다 (모듈 README "로컬 불변식").
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
