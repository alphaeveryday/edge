package com.edge.serving.error;

import com.edge.common.apipayload.code.BaseErrorCode;
import com.edge.common.apipayload.code.ErrorReasonDto;
import org.springframework.http.HttpStatus;

/**
 * serving 도메인 에러 코드 — jvm-common 규약: 공통 코드는 ErrorStatus, 도메인 코드는 앱 enum 소유.
 * 스펙(docs/contracts/serving-api.md): 400 = 필수 헤더 누락·잘못된 형식, 404 = 미상장 코드.
 */
public enum ServingErrorStatus implements BaseErrorCode {

	MISSING_CUSTOMER_HASH(HttpStatus.BAD_REQUEST, "SERV4001", "X-Customer-Hash 헤더가 필요합니다."),
	MISSING_CHANNEL(HttpStatus.BAD_REQUEST, "SERV4002", "X-Channel 헤더가 필요합니다."),
	INVALID_CHANNEL(HttpStatus.BAD_REQUEST, "SERV4003", "X-Channel 은 MTS, HTS, INTERNAL 중 하나여야 합니다."),
	INVALID_TRADE_DATE(HttpStatus.BAD_REQUEST, "SERV4004", "trade_date 는 yyyy-MM-dd 형식이어야 합니다."),
	UNKNOWN_ETF(HttpStatus.NOT_FOUND, "SERV4040", "알 수 없는 ETF 종목코드입니다.");

	private final HttpStatus httpStatus;
	private final String code;
	private final String message;

	ServingErrorStatus(HttpStatus httpStatus, String code, String message) {
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
