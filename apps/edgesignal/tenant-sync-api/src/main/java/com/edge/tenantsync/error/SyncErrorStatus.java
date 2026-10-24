package com.edge.tenantsync.error;

import com.edge.common.apipayload.code.BaseErrorCode;
import com.edge.common.apipayload.code.ErrorReasonDto;
import org.springframework.http.HttpStatus;

/**
 * sync 도메인 에러 코드 — jvm-common 규약: 공통 코드는 ErrorStatus, 도메인 코드는 앱 enum 소유.
 * 계약(sync-protocol.md) 에러 시맨틱과의 대응: 401·403·410 외 4xx 는 소비자 버그 신호(fail-loud).
 * 인증서 관련 코드(401/403)는 sync-auth 티켓에서 추가한다.
 */
public enum SyncErrorStatus implements BaseErrorCode {

	INVALID_AFTER(HttpStatus.BAD_REQUEST, "SYNC4001", "after 는 0 이상이어야 합니다 (첫 동기화는 0)."),
	INVALID_LIMIT(HttpStatus.BAD_REQUEST, "SYNC4002", "limit 은 1..500 이어야 합니다.");

	private final HttpStatus httpStatus;
	private final String code;
	private final String message;

	SyncErrorStatus(HttpStatus httpStatus, String code, String message) {
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
