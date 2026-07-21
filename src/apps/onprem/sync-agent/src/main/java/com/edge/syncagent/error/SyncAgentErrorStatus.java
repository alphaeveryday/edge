package com.edge.syncagent.error;

import com.edge.common.apipayload.code.BaseErrorCode;
import com.edge.common.apipayload.code.ErrorReasonDto;
import org.springframework.http.HttpStatus;

/**
 * sync-agent 도메인 에러 코드 — jvm-common 규약: 공통 코드는 ErrorStatus, 도메인 코드는 앱 enum 소유.
 * 502 = 업스트림(Cloud) 문제 신호 — 소비자(intake)는 다음 폴링에서 재시도한다.
 */
public enum SyncAgentErrorStatus implements BaseErrorCode {

	INVALID_AFTER(HttpStatus.BAD_REQUEST, "AGNT4001", "after 는 0 이상이어야 합니다."),
	UPSTREAM_FAILURE(HttpStatus.BAD_GATEWAY, "AGNT5021", "tenant-sync-api 호출에 실패했습니다."),
	CHECKSUM_MISMATCH(HttpStatus.BAD_GATEWAY, "AGNT5022", "번들 체크섬 검증에 실패했습니다 — 전달하지 않습니다."),
	UPSTREAM_MALFORMED(HttpStatus.BAD_GATEWAY, "AGNT5023", "tenant-sync-api 응답이 계약 형상을 위반했습니다."),
	// 4xx = 이 모듈의 요청이 잘못됐다는 신호(계약: 재시도로 낫지 않음) — 500 으로 구분 표면화.
	UPSTREAM_REJECTED(HttpStatus.INTERNAL_SERVER_ERROR, "AGNT5001", "tenant-sync-api 가 요청을 거부했습니다 — 소비자 버그 신호.");

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
