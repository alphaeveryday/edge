package com.edge.app.common.cursor;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.util.List;

/**
 * 커서 페이지 응답
 * 마지막 페이지에서 null 인 nextCursor 값
 * 전역 non_null 설정의 유일한 예외
 */
public record PageResponse<T>(List<T> items, @JsonInclude(JsonInclude.Include.ALWAYS) String nextCursor) {
}
