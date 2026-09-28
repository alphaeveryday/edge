package com.edge.app.common;

import com.edge.common.apipayload.code.BaseErrorCode;
import com.edge.common.apipayload.code.ErrorReasonDto;
import org.springframework.http.HttpStatus;

/**
 * 도메인 에러 코드. openapi.yaml 의 x-error-codes 와 1:1 (COMMON 넷은 jvm-common ErrorStatus 소유).
 * 형식 {도메인}{4|5}{순번}. 4 는 클라이언트 오류, 5 는 서버 오류, 순번은 도메인 안 단조 증가. HTTP 상태는 enum 이 따로 가진다.
 * 앱은 code 를 번역 없이 그대로 분기한다.
 */
public enum AppErrorStatus implements BaseErrorCode {
    ETF_NOT_FOUND(HttpStatus.NOT_FOUND, "ETF4001", "없는 ETF 입니다."),
    ANALYSIS_NOT_READY(HttpStatus.NOT_FOUND, "ANALYSIS4001", "분석이 아직 준비되지 않았습니다."),
    THEME_NOT_FOUND(HttpStatus.NOT_FOUND, "THEME4001", "없는 테마입니다."),
    ISSUE_NOT_FOUND(HttpStatus.NOT_FOUND, "ISSUE4001", "없는 이슈입니다."),
    POST_NOT_FOUND(HttpStatus.NOT_FOUND, "POST4001", "없는 글입니다."),
    POST_NOT_WATCHED_ETF(HttpStatus.BAD_REQUEST, "POST4002", "관심 ETF 에 대해서만 글을 쓸 수 있습니다."),
    POST_TOO_MANY_TAGS(HttpStatus.BAD_REQUEST, "POST4003", "태그는 최대 3개입니다."),
    WATCH_DEFAULT_GROUP_UNDELETABLE(HttpStatus.BAD_REQUEST, "WATCH4001", "기본 관심 그룹은 지울 수 없습니다."),
    WATCH_TOO_MANY_GROUPS(HttpStatus.BAD_REQUEST, "WATCH4002", "관심 그룹은 최대 10개입니다."),
    MEMBER_NOT_FOUND(HttpStatus.NOT_FOUND, "MEMBER4001", "가입되지 않은 이메일입니다."),
    MEMBER_ALREADY_EXISTS(HttpStatus.CONFLICT, "MEMBER4002", "이미 가입된 이메일입니다."),
    AUTH_BAD_CREDENTIALS(HttpStatus.UNAUTHORIZED, "AUTH4001", "이메일 또는 비밀번호가 맞지 않습니다."),
    VOTE_STORE_UNAVAILABLE(HttpStatus.SERVICE_UNAVAILABLE, "VOTE5001", "투표 저장소를 사용할 수 없습니다.");

    private final HttpStatus httpStatus;
    private final String code;
    private final String message;

    AppErrorStatus(HttpStatus httpStatus, String code, String message) {
        this.httpStatus = httpStatus;
        this.code = code;
        this.message = message;
    }

    public HttpStatus getHttpStatus() {
        return httpStatus;
    }

    public String getCode() {
        return code;
    }

    public String getMessage() {
        return message;
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
