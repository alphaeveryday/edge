package com.edge.superadmin.repository;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Repository;
import software.amazon.awssdk.services.sfn.SfnClient;
import software.amazon.awssdk.services.sfn.model.DescribeExecutionResponse;
import software.amazon.awssdk.services.sfn.model.ExecutionDoesNotExistException;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * {@link RunControlPlane} 의 Step Functions 구현(ALPHA-979 조각 2).
 *
 * <p><b>{@code DescribeExecution} 하나만 부른다</b> — ARN 은 원장이 준다({@code ops_pipeline_run}).
 * 태스크 역할 정책도 정확히 그 하나다(`infra/terraform/envs/dev/main.tf`). 호출을 늘리려면
 * <b>정책과 코드를 같이</b> 세라 — 안 그러면 {@code AccessDenied} 가 아래 폴백에 삼켜져
 * 리소스도 생기고 apply 도 초록인데 그 경로만 영영 안 돈다(ALPHA-671 선례).
 *
 * <p>🔴 <b>실패의 두 층을 가른다.</b>
 * <ul>
 *   <li><b>실행 하나가 없다</b>({@code ExecutionDoesNotExistException}) — 관측은 성공했고 그 실행이 없는
 *       것이다. 그 ARN 만 맵에서 빠진다. 계획만 되고 기동 못 한 런이 정확히 이 모양이다.</li>
 *   <li><b>제어면을 못 봤다</b>(자격증명·권한·네트워크) — 관측 자체가 실패다.
 *       {@link Observation#unavailable()} 로 <b>축 전체</b>를 비운다. 일부만 비우면 화면이
 *       "본 것 중엔 문제 없음"을 세우는데 사실은 아무것도 못 본 것이다.</li>
 * </ul>
 * 첫 호출이 권한 오류면 나머지 수십 건도 같은 이유로 실패한다 — 그래서 즉시 접는다.
 */
@Repository
public class SfnRunControlPlane implements RunControlPlane {

	private static final Logger log = LoggerFactory.getLogger(SfnRunControlPlane.class);

	private final SfnClient sfn;

	public SfnRunControlPlane(SfnClient sfn) {
		this.sfn = sfn;
	}

	@Override
	public Observation describe(List<String> executionArns) {
		Map<String, RunState> seen = new HashMap<>();
		for (String arn : executionArns) {
			try {
				DescribeExecutionResponse r =
						sfn.describeExecution(b -> b.executionArn(arn));
				/* status 는 SDK enum 의 **원문 문자열**을 쓴다 — `status()` 는 모르는 값을
				 * `UNKNOWN_TO_SDK_VERSION` 으로 접어 새 enum 을 삼킨다. 화면이 fail loud 하려면
				 * 서버가 원문을 그대로 올려야 한다. */
				seen.put(arn, new RunState(r.statusAsString(), utc(r.stopDate())));
			}
			catch (ExecutionDoesNotExistException e) {
				/* 관측 성공 · 그 실행 없음. 맵에서 빠지는 것이 곧 그 사실이다. */
			}
			catch (RuntimeException e) {
				/* 제어면을 못 봤다 — 축 전체를 비운다(부분 관측을 정상으로 그리지 않는다).
				 * 사유는 로그에만 둔다: 화면은 "조회했는데 못 봤다"까지만 말하고, 그 이상은
				 * 응답이 답할 사실이 아니다. */
				log.warn("AWS 제어면 조회 실패 — 런 축의 AWS 관측을 비운다 (arn={})", arn, e);
				return Observation.unavailable();
			}
		}
		return new Observation(OffsetDateTime.now(ZoneOffset.UTC), Map.copyOf(seen));
	}

	private static OffsetDateTime utc(java.time.Instant at) {
		return at == null ? null : at.atOffset(ZoneOffset.UTC);
	}
}
