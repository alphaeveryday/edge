package com.edge.common.exception;

import com.edge.common.apipayload.code.BaseErrorCode;
import com.edge.common.apipayload.code.ErrorReasonDto;

import lombok.Getter;

/**
 * 비즈니스 예외 베이스 — {@link BaseErrorCode} 를 실어 공통 어드바이스({@code ExceptionAdvice})가
 * 공통 응답 포맷으로 매핑한다. 공통 코드는 {@code ErrorStatus}, 도메인 코드는 각 앱 enum 이 소유한다.
 */
@Getter
public class GeneralException extends RuntimeException {

    private final transient BaseErrorCode code;

    public GeneralException(BaseErrorCode code) {
        super(code.getReasonHttpStatus().getMessage());
        this.code = code;
    }

    public ErrorReasonDto getErrorReason() {
        return this.code.getReason();
    }

    public ErrorReasonDto getErrorReasonHttpStatus() {
        return this.code.getReasonHttpStatus();
    }
}
