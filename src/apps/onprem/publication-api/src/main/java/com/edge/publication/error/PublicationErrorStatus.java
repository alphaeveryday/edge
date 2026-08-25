package com.edge.publication.error;

import com.edge.common.apipayload.code.BaseErrorCode;
import com.edge.common.apipayload.code.ErrorReasonDto;
import org.springframework.http.HttpStatus;

/**
 * publication 도메인 에러 코드 — jvm-common 규약: 공통 코드는 ErrorStatus, 도메인 코드는 앱 enum 소유.
 * 스펙(docs/contracts/publication-api.md): 404 = 미상장 코드. 400(trade_date 형식)은
 * 프레임워크 변환 실패 → 공통 코드 COMMON400 으로 수렴한다.
 * SERV4001~4003(고객 해시·채널 헤더, ADR-0053)·SERV4004(trade_date 형식 — 파싱을
 * 프레임워크 변환에 위임하며 폐지)는 은퇴 — 번호는 재사용하지 않는다.
 */
public enum PublicationErrorStatus implements BaseErrorCode {

	UNKNOWN_ETF(HttpStatus.NOT_FOUND, "SERV4040", "알 수 없는 ETF 종목코드입니다.");

	private final HttpStatus httpStatus;
	private final String code;
	private final String message;

	PublicationErrorStatus(HttpStatus httpStatus, String code, String message) {
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
