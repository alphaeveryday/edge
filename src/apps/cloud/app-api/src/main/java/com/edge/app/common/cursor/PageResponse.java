package com.edge.app.common.cursor;

import java.util.List;

/** 커서 페이지 응답. nextCursor 는 마지막 페이지면 null(키는 남긴다). */
public record PageResponse<T>(List<T> items, String nextCursor) {
}
