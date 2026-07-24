package com.edge.superadmin.mock;

import org.springframework.stereotype.Component;

/**
 * session 도메인 mock 데이터 — 사이드바·헤더가 쓰는 운영자 컨텍스트. 실세션
 * (SessionOperator)과 별개의 화면용 mock 이다 — 앱 전역 singleton 이라 사용자·세션별
 * 격리가 없다(한 세션의 이름 변경이 전 세션에 보인다 — mock 단계의 의도적
 * 트레이드오프). DB 연동 시 service 의 이 스토어 호출부를 실세션 조회로
 * 교체한다(ALPHA-515).
 */
@Component
public class AdminSessionMockStore {

	public record OperatorProfile(String name, String email, String role, String initials) {
	}

	private OperatorProfile profile = new OperatorProfile("EDGE 운영팀", "ops@edge.io", "Owner", "OP");

	public synchronized OperatorProfile current() {
		return profile;
	}

	public synchronized void updateDisplayName(String name) {
		profile = new OperatorProfile(name, profile.email(), profile.role(), profile.initials());
	}
}
