package com.edge.gateway.widget;

/**
 * 위젯 표준 응답의 카드. v1은 대표 카드 1개.
 * {@code title}은 optional pass-through — 없으면 {@code null}(프론트가 fallback label 사용).
 */
public record Card(String title, String description) {
}
