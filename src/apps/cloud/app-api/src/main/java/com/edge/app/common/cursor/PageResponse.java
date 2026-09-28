package com.edge.app.common.cursor;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.util.List;

/** 커서 페이지 응답. nextCursor 는 마지막 페이지면 null(키는 남긴다. 전역 non_null 의 유일한 예외). */
public record PageResponse<T>(List<T> items, @JsonInclude(JsonInclude.Include.ALWAYS) String nextCursor) {
}
