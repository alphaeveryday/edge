package com.edge.superadmin.dto;

import com.edge.superadmin.repository.MinuteStatusRepository.PriceJudgmentRow;
import com.fasterxml.jackson.annotation.JsonRawValue;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 가격 세션의 판정 근거(§33.12 로컬) — "정상 완료한 판정이 실제로 쓴 입력"을 보이는 조회.
 *
 * <ul>
 *   <li>{@code attempts} 가 비면 <b>기록 없음</b>이다 — 기록 도입 전이거나 성공하지 못한 시도다.
 *       무발화·미실행으로 추정하지 않는다.</li>
 *   <li>{@code inputRecord}: 판정 당시 입력(그 job 세대)의 artifact 이력 <b>기록</b>이 있으면 RECORDED
 *       (uri·checksum 은 기록된 값), 이력 행이 없으면 NO_HISTORY(이력 부재 — 원본 삭제·손상을 뜻하지
 *       않는다). 현재 window 나 최신 세대로 대체하지 않는다.</li>
 *   <li>{@code sourceRecheck}: 이 조회는 현재 보관된 artifact 본문을 읽지 않는다 — 항상 NOT_PERFORMED.
 *       이력 행이 있다고 파일이 지금 존재하거나 무결하다고 말하지 않는다(INPUT_UNAVAILABLE·
 *       CHECKSUM_MISMATCH 는 본문을 실제로 읽은 경로만 낼 수 있다).</li>
 *   <li>{@code windows} 는 <b>job 단위</b>다 — job 정체성은 session·window·세대·trigger_schema_version
 *       ({@code uq_price_window_job_identity})이라 같은 window·세대에 job 이 둘일 수 있고, 그때 둘을 합치면
 *       한쪽의 실패·기록 부재가 가려진다. {@code jobId} 로 묶는다.</li>
 *   <li>{@code judgedAt} 은 기록 INSERT 의 관측 시각이다. 커밋 순서·인과 순서가 아니다.</li>
 *   <li>{@code txAnchorLocked}: true 는 발화·회수 대상 앵커 행을 잠근 뒤 관측, false 는 무발화의
 *       비잠금 관측이다.</li>
 *   <li>정정 후 재계산은 제공하지 않는다({@code recomputation=NOT_GUARANTEED}) — 순차 처리가 따로
 *       보장된 구간이 아니면 결과를 보장할 수 없다(§33.12).</li>
 * </ul>
 */
public record MinuteJudgmentResponse(String sessionId, String recomputation, List<Window> windows) {

	public record Window(String jobId, OffsetDateTime windowStart, int windowGeneration, int jobGeneration,
			String jobStatus, int jobAttemptCount, boolean correctedAfter, String inputRecord,
			String artifactUri, String artifactChecksum, String sourceRecheck,
			List<Attempt> attempts) {
	}

	public record Attempt(int attempt, int redriveGeneration, OffsetDateTime judgedAt,
			boolean txAnchorLocked, String detectionPolicyVersion,
			@JsonRawValue String summary, @JsonRawValue String anchorsUsed,
			@JsonRawValue String txAnchor, @JsonRawValue String baselines, int judgedWithBaseline) {
	}

	public static MinuteJudgmentResponse from(String sessionId, List<PriceJudgmentRow> rows) {
		Map<String, List<PriceJudgmentRow>> byJob = new LinkedHashMap<>();
		for (PriceJudgmentRow r : rows) {
			byJob.computeIfAbsent(r.jobId(), k -> new ArrayList<>()).add(r);
		}
		List<Window> windows = new ArrayList<>();
		for (List<PriceJudgmentRow> group : byJob.values()) {
			PriceJudgmentRow head = group.get(0);
			List<Attempt> attempts = group.stream().filter(r -> r.attempt() != null)
					.map(r -> new Attempt(r.attempt(), r.redriveGeneration(), r.judgedAt(),
							Boolean.TRUE.equals(r.txAnchorLocked()), r.detectionPolicyVersion(),
							r.summaryJson(), r.anchorsUsedJson(), r.txAnchorJson(),
							r.baselinesJson() == null ? "{}" : r.baselinesJson(),
							r.judgedWithBaseline() == null ? 0 : r.judgedWithBaseline()))
					.toList();
			windows.add(new Window(head.jobId(), head.windowStart(), head.windowGeneration(), head.jobGeneration(),
					head.jobStatus(), head.jobAttemptCount(),
					head.windowGeneration() > head.jobGeneration(),
					head.artifactChecksum() == null ? "NO_HISTORY" : "RECORDED",
					head.artifactUri(), head.artifactChecksum(), "NOT_PERFORMED", attempts));
		}
		return new MinuteJudgmentResponse(sessionId, "NOT_GUARANTEED", windows);
	}
}
