package com.edge.superadmin.support;

import com.edge.superadmin.auth.SessionOperator;
import com.edge.superadmin.repository.AnalysisWriteRepository;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/**
 * standalone 컨트롤러 테스트용 {@link AnalysisWriteRepository} 손 페이크(레포 hand-fake 관례,
 * Mockito 미도입). 아는 런 ID 는 전이를 기록하고 true, 모르는 ID 는 false(404 로 이어짐)를 낸다 —
 * 실 원장 전이·감사 정합은 JdbcAnalysisWriteRepositoryIntegrationTest 소관이다. 기록한 호출로
 * 컨트롤러가 사유·작업자를 실제로 흘려보내는지 검증한다.
 */
public class FakeAnalysisWriteRepository implements AnalysisWriteRepository {

	/** 전이 한 건의 관측 — 사유·작업자가 서비스/컨트롤러를 통과했는지 확인용. */
	public record Call(String action, String runId, String result, String reason, SessionOperator actor) {
	}

	private final Set<String> knownRunIds;
	private final List<Call> calls = new ArrayList<>();

	public FakeAnalysisWriteRepository(Set<String> knownRunIds) {
		this.knownRunIds = knownRunIds;
	}

	@Override
	public boolean correct(String runId, String result, String reason, SessionOperator actor) {
		return recordIfKnown(new Call("CORRECT", runId, result, reason, actor));
	}

	@Override
	public boolean exclude(String runId, String reason, SessionOperator actor) {
		return recordIfKnown(new Call("EXCLUDE", runId, null, reason, actor));
	}

	@Override
	public boolean restore(String runId, String reason, SessionOperator actor) {
		return recordIfKnown(new Call("RESTORE", runId, null, reason, actor));
	}

	@Override
	public InvalidateOutcome invalidate(String runId, String reason, SessionOperator actor) {
		if (!knownRunIds.contains(runId)) {
			return InvalidateOutcome.RUN_NOT_FOUND;
		}
		if (unpublishedRunIds.contains(runId)) {
			return InvalidateOutcome.NOT_PUBLISHED;
		}
		calls.add(new Call("INVALIDATE", runId, null, reason, actor));
		return InvalidateOutcome.INVALIDATED;
	}

	/** 아는 런이지만 게시 상태가 아닌 것으로 취급할 ID — 409 경로 검증용. */
	public void markUnpublished(String runId) {
		unpublishedRunIds.add(runId);
	}

	private final Set<String> unpublishedRunIds = new java.util.HashSet<>();

	private boolean recordIfKnown(Call call) {
		if (!knownRunIds.contains(call.runId())) {
			return false;
		}
		calls.add(call);
		return true;
	}

	public List<Call> calls() {
		return calls;
	}
}
