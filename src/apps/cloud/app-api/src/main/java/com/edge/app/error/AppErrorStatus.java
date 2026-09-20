package com.edge.app.error;

import com.edge.common.apipayload.code.BaseErrorCode;
import com.edge.common.apipayload.code.ErrorReasonDto;
import org.springframework.http.HttpStatus;

public enum AppErrorStatus implements BaseErrorCode {
    VOTE_STORE_UNAVAILABLE(HttpStatus.SERVICE_UNAVAILABLE, "VOTE5030", "투표 저장소를 사용할 수 없습니다.");

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
        return ErrorReasonDto.builder().message(message).code(code).isSuccess(false).build();
    }

    @Override
    public ErrorReasonDto getReasonHttpStatus() {
        return ErrorReasonDto.builder().message(message).code(code).isSuccess(false).httpStatus(httpStatus).build();
    }
}
