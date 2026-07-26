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
	INVALID_REQUEST(HttpStatus.BAD_REQUEST, "CNSL4003", "요청 값이 올바르지 않습니다."),
	// 로그인 실패 사유는 구분 없이 하나의 코드 — 계정 존재 여부 노출 방지.
	LOGIN_INVALID(HttpStatus.UNAUTHORIZED, "CNSL4010", "이메일 또는 비밀번호가 올바르지 않습니다."),
	// CNSL4011(로그인 필요)·CNSL4030(권한 없음)은 ConsoleAuthFilter 가 직접 쓴다
	// (필터는 advice 를 타지 않음) — 코드 공간만 여기 예약해 둔다.
	// 직무 분리(permission-matrix.md): TA 가 스스로에게 CR 을 부여해 검수·정책 권한을
	// 얻는 우회 차단 — 인가 실패가 아니라 도메인 금지라 필터가 아닌 서비스가 던진다.
	SELF_ROLE_CHANGE(HttpStatus.FORBIDDEN, "CNSL4031", "자기 자신의 역할은 변경할 수 없습니다."),
	REVIEW_ITEM_NOT_FOUND(HttpStatus.NOT_FOUND, "CNSL4040", "검수 항목을 찾을 수 없습니다."),
	EXPLANATION_NOT_FOUND(HttpStatus.NOT_FOUND, "CNSL4041", "가격 변동 설명을 찾을 수 없습니다."),
	BANNED_WORD_NOT_FOUND(HttpStatus.NOT_FOUND, "CNSL4042", "금칙어를 찾을 수 없습니다."),
	SCOPE_TARGET_NOT_FOUND(HttpStatus.NOT_FOUND, "CNSL4043", "제공 범위 대상(시장·종목)을 찾을 수 없습니다."),
	MEMBER_NOT_FOUND(HttpStatus.NOT_FOUND, "CNSL4044", "사용자를 찾을 수 없습니다."),
	NOT_IN_REVIEW(HttpStatus.CONFLICT, "CNSL4090", "검수 대기(REVIEW_REQUIRED) 상태가 아닙니다 — 이미 처리됐을 수 있습니다."),
	GRAIN_OCCUPIED(HttpStatus.CONFLICT, "CNSL4091", "같은 종목·거래일에 게시분이 이미 있습니다 — 기존 게시를 내린 뒤 승인하세요."),
	NOT_PUBLISHABLE(HttpStatus.CONFLICT, "CNSL4092", "게시 정보(ticker)가 없어 승인할 수 없습니다."),
	DUPLICATE_MEMBER_EMAIL(HttpStatus.CONFLICT, "CNSL4093", "이미 등록된 이메일입니다."),
	LAST_ADMIN(HttpStatus.CONFLICT, "CNSL4094", "마지막 활성 관리자는 비활성화하거나 역할을 변경할 수 없습니다."),
	ROLE_CONFLICT(HttpStatus.CONFLICT, "CNSL4095", "역할이 이미 변경됐습니다 — 목록을 새로고침한 뒤 다시 시도하세요.");

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
