package com.edge.superadmin.support;

import com.edge.superadmin.repository.AnalysisRepository;

import java.util.List;

/**
 * standalone 컨트롤러 테스트용 {@link AnalysisRepository} 손 페이크(레포 hand-fake 관례,
 * Mockito 미도입). 주입한 원장 행을 그대로 돌려준다 — 조회 SQL 정합은
 * JdbcAnalysisRepositoryIntegrationTest 소관이다.
 */
public class FakeAnalysisRepository implements AnalysisRepository {

	private final List<AnalysisRow> rows;

	public FakeAnalysisRepository(List<AnalysisRow> rows) {
		this.rows = rows;
	}

	@Override
	public List<AnalysisRow> list() {
		return rows;
	}
}
