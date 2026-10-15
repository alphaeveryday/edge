package com.edge.common.apipayload;

import com.edge.common.apipayload.code.BaseCode;
import com.edge.common.apipayload.code.status.SuccessStatus;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonPropertyOrder;

/**
 * 공통 응답 포맷 — 와이어 키는 정확히 {@code isSuccess, code, message[, result]} 다.
 *
 * <p>record 인 이유(ALPHA-605): 종전 class + Lombok 구현은 {@code @JsonProperty("isSuccess")}
 * 필드와 Lombok is-게터(Jackson 암묵명 {@code success})가 별개 프로퍼티로 갈라져 전 모듈 응답에
 * 중복 키 {@code "success"} 가 실렸다. record 컴포넌트는 접근자 이름을 빈 규약(is-접두 제거)으로
 * 재해석하지 않고 컴포넌트 이름 그대로 쓰므로 중복이 원천적으로 생기지 않는다 — 이 형상은
 * {@code ApiResponseSerializationTest} 가 키 집합 전수 비교로 고정한다.
 */
@JsonPropertyOrder({"isSuccess", "code", "message", "result"})
public record ApiResponse<T>(
		@JsonProperty("isSuccess") boolean isSuccess,
		String code,
		String message,
		@JsonInclude(JsonInclude.Include.NON_NULL) T result) {

	public static <T> ApiResponse<T> onSuccess(T result) {
		return new ApiResponse<>(true, SuccessStatus.OK.getCode(), SuccessStatus.OK.getMessage(), result);
	}

	public static <T> ApiResponse<T> of(BaseCode code, T result) {
		return new ApiResponse<>(true, code.getReasonHttpStatus().getCode(),
				code.getReasonHttpStatus().getMessage(), result);
	}

	public static <T> ApiResponse<T> onFailure(String code, String message, T data) {
		return new ApiResponse<>(false, code, message, data);
	}
}
