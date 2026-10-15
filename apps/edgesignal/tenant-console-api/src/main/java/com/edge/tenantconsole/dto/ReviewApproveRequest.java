package com.edge.tenantconsole.dto;

/**
 * 일반 승인 요청(ALPHA-437) — 선택 의견(note)만 받는다. 수정 승인은 전용 라우트
 * (approve-edited·ReviewEditedApproveRequest)다 — 한 라우트의 선택 바디로 겸하면
 * unknown 필드 무시(Jackson 기본) 탓에 편집 필드 오타가 일반 승인으로 강등된다.
 */
public record ReviewApproveRequest(String note) {
}
