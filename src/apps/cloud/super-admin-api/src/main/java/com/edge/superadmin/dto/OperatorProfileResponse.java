package com.edge.superadmin.dto;

import com.edge.superadmin.mock.AdminSessionMockStore.OperatorProfile;

/**
 * 콘솔 운영자 컨텍스트 응답(사이드바·헤더). 필드는 super-admin-ui session 타입과
 * 동일한 camelCase. mock 스토어 record(OperatorProfile)와 형식이 같아도 와이어
 * 형은 별도 타입으로 둔다.
 */
public record OperatorProfileResponse(String name, String email, String role, String initials) {

	public static OperatorProfileResponse from(OperatorProfile profile) {
		return new OperatorProfileResponse(profile.name(), profile.email(), profile.role(),
				profile.initials());
	}
}
