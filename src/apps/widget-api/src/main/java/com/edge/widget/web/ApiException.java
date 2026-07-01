package com.edge.widget.web;

import org.springframework.http.HttpStatus;

/**
 * 웹 계층 도메인 예외 — 안정적 {@code code} 와 HTTP status 를 실어
 * {@link GlobalExceptionHandler} 가 공통 봉투 {@code ApiResponse.fail(code, message)} 로 매핑한다.
 *
 * <p>Spring 타입({@link HttpStatus})을 쓰므로 jvm-common 이 아니라 <b>앱 웹 계층</b>에 둔다
 * (jvm-common 은 프레임워크 무의존 원칙). 이 패턴이 여러 앱에서 반복되면 그때 common-web
 * 모듈로 승격을 검토한다.
 */
public class ApiException extends RuntimeException {

    private final String code;
    private final HttpStatus status;

    public ApiException(HttpStatus status, String code, String message) {
        super(message);
        this.status = status;
        this.code = code;
    }

    public String getCode() {
        return code;
    }

    public HttpStatus getStatus() {
        return status;
    }
}
