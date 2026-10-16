package com.edge.common.exception;

import com.edge.common.apipayload.ApiResponse;
import com.edge.common.apipayload.code.ErrorReasonDto;
import com.edge.common.apipayload.code.status.ErrorStatus;

import jakarta.validation.ConstraintViolationException;

import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.context.request.WebRequest;
import org.springframework.web.servlet.mvc.method.annotation.ResponseEntityExceptionHandler;

/**
 * 예외 → 공통 응답 포맷({@link ApiResponse}) 매핑의 단일 지점.
 * 상속한 Spring MVC 예외 핸들러 전부가 마지막에 거치는 {@code handleExceptionInternal}(깔때기)에서
 * 본문을 공통 포맷으로 교체하므로, 프레임워크 예외(깨진 JSON·파라미터 누락·405 등)도 공통 포맷으로 나간다.
 * 자체 핸들러는 부모가 모르는 세 갈래만 둔다 — 비즈니스({@link GeneralException})·서비스 계층 검증
 * ({@link ConstraintViolationException})·그 외 전부(catch-all 500).
 * 등록은 {@link ExceptionAdviceAutoConfiguration}(서블릿 웹 앱 한정)이 담당한다.
 *
 * <p>앱 커스텀: 상태·코드·메시지 커스텀은 앱의 {@code *ErrorStatus} enum + GeneralException 으로 충분하다.
 * 그 이상(서드파티 예외 매핑·특수 헤더)이 필요할 때만 앱에 별도 advice 를 두되, 반드시 {@code @Order} 로
 * 우선순위를 높여라 — advice 간에는 구체성 비교 없이 우선순위 순으로 첫 매칭 advice 가 채택되므로,
 * 순위를 안 높이면 이 클래스의 catch-all 이 먼저 잡는다(이 클래스는 의도적으로 기본 순위=최하위).
 */
@Slf4j
@RestControllerAdvice
public class ExceptionAdvice extends ResponseEntityExceptionHandler {

    @ExceptionHandler(GeneralException.class)
    public ResponseEntity<Object> handleGeneral(GeneralException ex, WebRequest request) {
        ErrorReasonDto reason = ex.getErrorReasonHttpStatus();
        ApiResponse<Object> body = ApiResponse.onFailure(reason.getCode(), reason.getMessage(), null);
        return handleExceptionInternal(ex, body, HttpHeaders.EMPTY, reason.getHttpStatus(), request);
    }

    // 서비스 계층 @Validated 검증 실패용 — 컨트롤러 파라미터 검증은
    // HandlerMethodValidationException 으로 나와 부모(→깔때기)가 처리한다.
    @ExceptionHandler(ConstraintViolationException.class)
    public ResponseEntity<Object> handleConstraintViolation(ConstraintViolationException ex, WebRequest request) {
        return handleExceptionInternal(ex, failure(ErrorStatus._BAD_REQUEST),
                HttpHeaders.EMPTY, ErrorStatus._BAD_REQUEST.getHttpStatus(), request);
    }

    // 예상하지 못한 예외의 마지막 그물 — 원문 메시지는 내부 정보라 응답에 싣지 않고 로그로만 남긴다.
    @ExceptionHandler(Exception.class)
    public ResponseEntity<Object> handleUnexpected(Exception ex, WebRequest request) {
        log.error("처리되지 않은 예외", ex);
        return handleExceptionInternal(ex, failure(ErrorStatus._INTERNAL_SERVER_ERROR),
                HttpHeaders.EMPTY, ErrorStatus._INTERNAL_SERVER_ERROR.getHttpStatus(), request);
    }

    // 깔때기 — 부모의 모든 핸들러와 위 핸들러들이 응답 직전에 여기를 거친다.
    // 이미 공통 포맷이면 통과, 아니면(부모가 만든 ProblemDetail·null) 상태코드 기반 공통 포맷으로 교체.
    @Override
    protected ResponseEntity<Object> handleExceptionInternal(
            Exception ex, Object body, HttpHeaders headers, HttpStatusCode statusCode, WebRequest request) {
        if (!(body instanceof ApiResponse)) {
            body = failureOf(statusCode);
        }
        return super.handleExceptionInternal(ex, body, headers, statusCode, request);
    }

    private static ApiResponse<Object> failureOf(HttpStatusCode statusCode) {
        return switch (statusCode.value()) {
            case 400 -> failure(ErrorStatus._BAD_REQUEST);
            case 401 -> failure(ErrorStatus._UNAUTHORIZED);
            case 403 -> failure(ErrorStatus._FORBIDDEN);
            case 500 -> failure(ErrorStatus._INTERNAL_SERVER_ERROR);
            // ErrorStatus 에 없는 상태(404·405·415 등)는 "COMMON"+상태코드 작명 규칙을 연장한다.
            default -> {
                HttpStatus resolved = HttpStatus.resolve(statusCode.value());
                yield ApiResponse.onFailure("COMMON" + statusCode.value(),
                        resolved != null ? resolved.getReasonPhrase() : "요청을 처리할 수 없습니다.", null);
            }
        };
    }

    private static ApiResponse<Object> failure(ErrorStatus status) {
        return ApiResponse.onFailure(status.getCode(), status.getMessage(), null);
    }
}
