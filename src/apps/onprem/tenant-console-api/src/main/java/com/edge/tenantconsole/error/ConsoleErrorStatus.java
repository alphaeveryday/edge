package com.edge.tenantconsole.error;

import com.edge.common.apipayload.code.BaseErrorCode;
import com.edge.common.apipayload.code.ErrorReasonDto;
import org.springframework.http.HttpStatus;

/**
 * tenant-console 도메인 에러 코드 — jvm-common 규약: 공통 코드는 ErrorStatus, 도메인 코드는 앱 enum 소유.
 * 409 = 상태 전이 충돌(이미 검수됨·grain 선점) — 화면은 목록 새로고침으로 수렴한다.
 */
public enum ConsoleErrorStatus implements BaseErrorCode {

	REASON_REQUIRED(HttpStatus.BAD_REQUEST, "CNSL4001", "반려 사유는 비어 있을 수 없습니다."),
	INVALID_STATUS_FILTER(HttpStatus.BAD_REQUEST, "CNSL4002", "지원하지 않는 상태 필터입니다."),
	REVIEW_ITEM_NOT_FOUND(HttpStatus.NOT_FOUND, "CNSL4040", "검수 항목을 찾을 수 없습니다."),
	NOT_IN_REVIEW(HttpStatus.CONFLICT, "CNSL4090", "검수 대기(REVIEW_REQUIRED) 상태가 아닙니다 — 이미 처리됐을 수 있습니다."),
	GRAIN_OCCUPIED(HttpStatus.CONFLICT, "CNSL4091", "같은 종목·거래일에 게시분이 이미 있습니다 — 기존 게시를 내린 뒤 승인하세요."),
	NOT_PUBLISHABLE(HttpStatus.CONFLICT, "CNSL4092", "게시 정보(ticker)가 없어 승인할 수 없습니다.");

	private final HttpStatus httpStatus;
	private final String code;
	private final String message;

	ConsoleErrorStatus(HttpStatus httpStatus, String code, String message) {
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
