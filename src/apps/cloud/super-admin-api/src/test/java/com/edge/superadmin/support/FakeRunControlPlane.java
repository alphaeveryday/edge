package com.edge.superadmin.support;

import com.edge.superadmin.repository.RunControlPlane;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * standalone 컨트롤러 테스트용 {@link RunControlPlane} 손 페이크(레포 hand-fake 관례).
 *
 * <p><b>물어본 ARN 을 기록한다</b> — 서버가 제어면에 실제로 물었는지, 물었다면 <b>무엇을</b>
 * 물었는지가 이 축의 계약이다. 안 기록하면 "ARN 을 안 넘겨도 통과하는" 테스트가 된다(Rule 9).
 */
public class FakeRunControlPlane implements RunControlPlane {

	/** 관측 시각이 {@code null} 이면 "제어면을 못 봤다" — 미배선(키 부재)과 다른 사실이다. */
	private final Observation observation;

	/** 마지막 요청에서 물어본 ARN. 순서·중복까지 그대로 담는다. */
	public final List<String> asked = new ArrayList<>();

	public FakeRunControlPlane(Observation observation) {
		this.observation = observation;
	}

	/** 제어면을 못 본 경우 — 자격증명·권한·장애. */
	public static FakeRunControlPlane unavailable() {
		return new FakeRunControlPlane(Observation.unavailable());
	}

	/** 관측 성공. {@code byArn} 에 없는 ARN 은 "그 실행이 없다"이다. */
	public static FakeRunControlPlane seeing(OffsetDateTime at, Map<String, RunState> byArn) {
		return new FakeRunControlPlane(new Observation(at, byArn));
	}

	@Override
	public Observation describe(List<String> executionArns) {
		asked.clear();
		asked.addAll(executionArns);
		return observation;
	}
}
