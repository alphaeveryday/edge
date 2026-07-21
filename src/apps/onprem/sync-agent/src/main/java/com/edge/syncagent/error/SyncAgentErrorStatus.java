package com.edge.syncagent.error;

import com.edge.common.apipayload.code.BaseErrorCode;
import com.edge.common.apipayload.code.ErrorReasonDto;
import org.springframework.http.HttpStatus;

/**
 * sync-agent 도메인 에러 코드. 업스트림·검증 실패는 전부 502(BAD_GATEWAY)로 표면화한다 —
 * DMZ 프록시가 신뢰할 수 없는 응답을 내부로 흘리지 않도록 fail-loud(ADR-0036).
 */
public enum SyncAgentErrorStatus implements BaseErrorCode {

	CHECKSUM_MISMATCH(HttpStatus.BAD_GATEWAY, "SYNCAGENT5021", "번들 체크섬 검증 실패 — 수신 바이트 해시가 X-Bundle-Checksum 과 불일치"),
	MISSING_CHECKSUM(HttpStatus.BAD_GATEWAY, "SYNCAGENT5022", "업스트림 200 응답에 X-Bundle-Checksum 헤더가 없음"),
	UPSTREAM_ERROR(HttpStatus.BAD_GATEWAY, "SYNCAGENT5023", "업스트림 Tenant Sync API 호출 실패");

	private final HttpStatus httpStatus;
	private final String code;
	private final String message;

	SyncAgentErrorStatus(HttpStatus httpStatus, String code, String message) {
		this.httpStatus = httpStatus;
		this.code = code;
		this.message = message;
	}

	@Override
	public ErrorReasonDto getReason() {
		return ErrorReasonDto.builder().isSuccess(false).code(code).message(message).build();
	}

	@Override
	public ErrorReasonDto getReasonHttpStatus() {
		return ErrorReasonDto.builder().httpStatus(httpStatus).isSuccess(false).code(code).message(message).build();
	}
}
