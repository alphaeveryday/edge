package com.edge.widget.widget;

import com.fasterxml.jackson.annotation.JsonProperty;

/** 위젯 표준 응답의 fallback 블록. success/empty에서는 {@code isFallback=false}, 나머지 null. */
public record Fallback(@JsonProperty("isFallback") boolean isFallback, String reason, String basedAt) {
}
