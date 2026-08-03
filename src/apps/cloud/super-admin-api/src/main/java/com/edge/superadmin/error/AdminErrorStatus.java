package com.edge.superadmin.error;

import com.edge.common.apipayload.code.BaseErrorCode;
import com.edge.common.apipayload.code.ErrorReasonDto;
import org.springframework.http.HttpStatus;

/**
 * super-admin 도메인 에러 코드 — jvm-common 규약: 공통 코드는 ErrorStatus, 도메인 코드는 앱 enum 소유.
 */
public enum AdminErrorStatus implements BaseErrorCode {

	INVALID_REQUEST(HttpStatus.BAD_REQUEST, "ADMN4001", "요청 값이 올바르지 않습니다."),
	// 로그인 실패 사유는 구분 없이 하나의 코드 — 계정 존재 여부 노출 방지.
	LOGIN_INVALID(HttpStatus.UNAUTHORIZED, "ADMN4010", "이메일 또는 비밀번호가 올바르지 않습니다."),
	// ADMN4011(로그인 필요)·ADMN4030(권한 없음)은 AdminAuthFilter 가 직접 쓴다
	// (필터는 advice 를 타지 않음) — 코드 공간만 여기 예약해 둔다.
	ANALYSIS_NOT_FOUND(HttpStatus.NOT_FOUND, "ADMN4040", "분석 건을 찾을 수 없습니다."),
	// 빈 리포트로 대신하지 않는 이유: 지목한 런이 없는 것과 원장이 비어 있는 것은 다른 사실이다.
	RUN_NOT_FOUND(HttpStatus.NOT_FOUND, "ADMN4041", "해당 파이프라인 실행을 찾을 수 없습니다."),
	// 404 와 구분하는 이유: 런은 있는데 게시 상태가 아닌 것(DRAFT·이미 무효화)은 대상 부재가
	// 아니라 상태 충돌이다 — 재호출 멱등 신호(이미 내려감)로도 쓰인다.
	ANALYSIS_NOT_PUBLISHED(HttpStatus.CONFLICT, "ADMN4090", "게시 상태가 아니라 무효화할 수 없습니다.");

	private final HttpStatus httpStatus;
	private final String code;
	private final String message;

	AdminErrorStatus(HttpStatus httpStatus, String code, String message) {
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
