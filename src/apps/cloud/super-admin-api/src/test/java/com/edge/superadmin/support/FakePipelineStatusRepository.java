package com.edge.superadmin.support;

import com.edge.superadmin.repository.PipelineStatusRepository;

import java.util.List;
import java.util.Optional;

/**
 * standalone 컨트롤러 테스트용 {@link PipelineStatusRepository} 손 페이크(레포 hand-fake 관례,
 * Mockito 미도입). 주입한 런을 그대로 돌려주고, null 이면 "원장에 런 없음"을 흉내낸다.
 *
 * <p>{@link #runByKey}는 <b>키가 실제로 맞을 때만</b> 돌려준다 — 무조건 같은 런을 주면 "없는 런은
 * 404" 라는 계약을 검증하는 테스트가 구조적으로 통과할 수 없다(Rule 9).
 */
public class FakePipelineStatusRepository implements PipelineStatusRepository {

	private final PipelineRunStatus run;
	private final List<GridSlot> gridSlots;

	public FakePipelineStatusRepository(PipelineRunStatus run) {
		this(run, List.of());
	}

	public FakePipelineStatusRepository(PipelineRunStatus run, List<GridSlot> gridSlots) {
		this.run = run;
		this.gridSlots = gridSlots;
	}

	@Override
	public Optional<PipelineRunStatus> latestRun() {
		return Optional.ofNullable(run);
	}

	@Override
	public Optional<PipelineRunStatus> runByKey(String runKey) {
		return latestRun().filter(r -> r.runKey().equals(runKey));
	}

	@Override
	public List<GridSlot> grid(int days) {
		return gridSlots;
	}
}
