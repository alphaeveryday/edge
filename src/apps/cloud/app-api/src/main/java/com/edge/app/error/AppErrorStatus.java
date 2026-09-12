package com.edge.app.error;

import com.edge.common.apipayload.code.BaseErrorCode;
import com.edge.common.apipayload.code.ErrorReasonDto;
import org.springframework.http.HttpStatus;

public enum AppErrorStatus implements BaseErrorCode {

	WITHDRAW_REASON_REQUIRED(HttpStatus.BAD_REQUEST, "APP4001", "철회 사유는 필수입니다."),
	VOTE_IN_PROGRESS(HttpStatus.TOO_MANY_REQUESTS, "APP4290", "이전 투표를 처리 중입니다. 잠시 후 다시 시도해 주세요."),
	MEMBER_NOT_FOUND(HttpStatus.NOT_FOUND, "APP4040", "존재하지 않는 유저입니다."),
	FORECAST_NOT_FOUND(HttpStatus.NOT_FOUND, "APP4041", "존재하지 않는 전망입니다."),
	FORECAST_NOT_OPEN(HttpStatus.CONFLICT, "APP4090", "마감되었거나 철회된 전망입니다."),
	FORECAST_ALREADY_WITHDRAWN(HttpStatus.CONFLICT, "APP4091", "이미 철회된 전망입니다.");

	private final HttpStatus httpStatus;
	private final String code;
	private final String message;

	AppErrorStatus(HttpStatus httpStatus, String code, String message) {
		this.httpStatus = httpStatus;
		this.code = code;
		this.message = message;
	}

	@Override
	public ErrorReasonDto getReason() {
		return ErrorReasonDto.builder()
				.message(message)
				.code(code)
				.isSuccess(false)
				.build();
	}

	@Override
	public ErrorReasonDto getReasonHttpStatus() {
		return ErrorReasonDto.builder()
				.message(message)
				.code(code)
				.isSuccess(false)
				.httpStatus(httpStatus)
				.build();
	}
}
