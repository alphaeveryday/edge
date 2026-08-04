package com.edge.superadmin.support;

import com.edge.superadmin.auth.SessionOperator;
import com.edge.superadmin.repository.AnalysisWriteRepository;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * standalone 컨트롤러 테스트용 {@link AnalysisWriteRepository} 손 페이크(레포 hand-fake 관례,
 * Mockito 미도입). 실 원장 전이·발번·감사 정합은 JdbcAnalysisWriteRepositoryIntegrationTest
 * 소관이다. 기록한 호출로 컨트롤러가 사유·작업자를 실제로 흘려보내는지 검증한다.
 */
public class FakeAnalysisWriteRepository implements AnalysisWriteRepository {

	/** 전이 한 건의 관측 — 사유·작업자가 서비스/컨트롤러를 통과했는지 확인용. */
	public record Call(String action, String runId, String reason, SessionOperator actor) {
	}

	private final Set<String> knownRunIds;
	private final Set<String> unpublishedRunIds = new HashSet<>();
	private final List<Call> calls = new ArrayList<>();

	public FakeAnalysisWriteRepository(Set<String> knownRunIds) {
		this.knownRunIds = knownRunIds;
	}

	@Override
	public InvalidateOutcome invalidate(String runId, String reason, SessionOperator actor) {
		if (!knownRunIds.contains(runId)) {
			return InvalidateOutcome.RUN_NOT_FOUND;
		}
		if (unpublishedRunIds.contains(runId)) {
			return InvalidateOutcome.NOT_PUBLISHED;
		}
		calls.add(new Call("INVALIDATE", runId, reason, actor));
		return InvalidateOutcome.INVALIDATED;
	}

	/** 아는 런이지만 게시 상태가 아닌 것으로 취급할 ID — 409 경로 검증용. */
	public void markUnpublished(String runId) {
		unpublishedRunIds.add(runId);
	}

	public List<Call> calls() {
		return calls;
	}
}
