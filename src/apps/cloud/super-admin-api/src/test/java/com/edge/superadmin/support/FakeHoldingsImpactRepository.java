package com.edge.superadmin.support;

import com.edge.superadmin.repository.HoldingsImpactRepository;

/**
 * standalone 컨트롤러 테스트용 손 페이크. runKey 가 실제로 맞을 때만 돌려준다 —
 * 무조건 같은 Impact 를 주면 "없는 런은 404" 계약을 검증할 수 없다(Rule 9).
 * null 주입 = 원장에 etf-daily 런 없음.
 */
public class FakeHoldingsImpactRepository implements HoldingsImpactRepository {

	private final Impact impact;

	public FakeHoldingsImpactRepository() {
		this(null);
	}

	public FakeHoldingsImpactRepository(Impact impact) {
		this.impact = impact;
	}

	@Override
	public Impact impact(String runKey) {
		if (impact == null) {
			return null;
		}
		return (runKey == null || impact.runKey().equals(runKey)) ? impact : null;
	}
}
