package com.edge.tenantconsole.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * 최종 문구 요청 — 게시본 최종 제공 문면. `final` 은 Java 예약어라 필드는 finalText,
 * JSON 키는 @JsonProperty 로 맞춘다. ExplanationController PATCH …/final.
 */
public record FinalRequest(@JsonProperty("final") String finalText) {
}
