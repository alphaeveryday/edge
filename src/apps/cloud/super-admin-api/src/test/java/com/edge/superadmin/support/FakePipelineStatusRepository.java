package com.edge.superadmin.support;

import com.edge.superadmin.repository.PipelineStatusRepository;

import java.util.Optional;

/**
 * standalone 컨트롤러 테스트용 {@link PipelineStatusRepository} 손 페이크(레포 hand-fake 관례,
 * Mockito 미도입). 주입한 런을 그대로 돌려주고, null 이면 "원장에 런 없음"을 흉내낸다.
 */
public class FakePipelineStatusRepository implements PipelineStatusRepository {

	private final PipelineRunStatus run;

	public FakePipelineStatusRepository(PipelineRunStatus run) {
		this.run = run;
	}

	@Override
	public Optional<PipelineRunStatus> latestRun() {
		return Optional.ofNullable(run);
	}
}
