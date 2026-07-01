package com.edge.common.response;

/**
 * 공통 에러 표현 — 프레임워크 비의존 순수 record.
 *
 * <p>{@code code} 는 클라이언트가 분기할 수 있는 안정적 문자열 코드(예: "TENANT_NOT_FOUND").
 * 지금은 String 으로 열어 두고, 코드 집합이 굳어지면 공유 enum/카탈로그로 승격을 검토한다.
 */
public record ApiError(String code, String message) {
}
