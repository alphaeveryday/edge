package com.edge.syncagent.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.common.apipayload.code.ErrorReasonDto;
import com.edge.common.apipayload.code.status.ErrorStatus;
import com.edge.common.exception.GeneralException;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

@RestControllerAdvice
public class GlobalExceptionHandler {

	@ExceptionHandler(GeneralException.class)
	public ResponseEntity<ApiResponse<Void>> handleGeneral(GeneralException e) {
		ErrorReasonDto reason = e.getErrorReasonHttpStatus();
		return ResponseEntity.status(reason.getHttpStatus())
				.body(ApiResponse.onFailure(reason.getCode(), reason.getMessage(), null));
	}

	@ExceptionHandler({MissingServletRequestParameterException.class, MethodArgumentTypeMismatchException.class})
	public ResponseEntity<ApiResponse<Void>> handleBadRequest(Exception e) {
		ErrorReasonDto reason = ErrorStatus._BAD_REQUEST.getReasonHttpStatus();
		return ResponseEntity.status(reason.getHttpStatus())
				.body(ApiResponse.onFailure(reason.getCode(), reason.getMessage(), null));
	}

	@ExceptionHandler(Exception.class)
	public ResponseEntity<ApiResponse<Void>> handleUnknown(Exception e) {
		ErrorReasonDto reason = ErrorStatus._INTERNAL_SERVER_ERROR.getReasonHttpStatus();
		return ResponseEntity.status(reason.getHttpStatus())
				.body(ApiResponse.onFailure(reason.getCode(), reason.getMessage(), null));
	}
}
