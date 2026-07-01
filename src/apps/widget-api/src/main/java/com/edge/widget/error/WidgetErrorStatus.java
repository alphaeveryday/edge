package com.edge.widget.error;

import com.edge.common.apipayload.code.BaseErrorCode;
import com.edge.common.apipayload.code.ErrorReasonDto;
import org.springframework.http.HttpStatus;

import lombok.AllArgsConstructor;
import lombok.Getter;

/**
 * widget-api 도메인 에러 코드. 공통 코드는 jvm-common 의 {@code ErrorStatus}, 도메인 코드는 여기.
 */
@Getter
@AllArgsConstructor
public enum WidgetErrorStatus implements BaseErrorCode {

    WIDGET_NOT_FOUND(HttpStatus.NOT_FOUND, "WIDGET4001", "위젯을 찾을 수 없습니다.");

    private final HttpStatus httpStatus;
    private final String code;
    private final String message;

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
