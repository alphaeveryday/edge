package com.edge.superadmin.repository;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

/**
 * 슈퍼 어드민 콘솔 규칙 엔진이 읽는 <b>사실</b>(ALPHA-738 · docs/contracts/console-facts-api.md).
 *
 * <p>여기서 위반을 판정하지 않는다 — 규칙은 프론트의 순수 함수이고 이 인터페이스는 그 입력만
 * 낸다. 원장 어휘({@code orchestration_status}·{@code task_outcome} 등)를 다섯 번째 어휘로
 * 뭉개지 않는 것도 {@link PipelineStatusRepository} 와 같다.
 *
 * <p><b>계측이 없는 축은 이 인터페이스에 없다</b>(계약 §부재를 싣는 규약 — "필드를 안 보낸다").
 * 축은 조각별로 하나씩 붙는다 — 지금은 <b>조회 창 + 런 축</b>이고, 작업·데이터셋·산출·경계는
 * 뒤따른다. 축이 붙기 전까지 그 필드는 응답에 <b>아예 없다</b>(빈 배열이 아니다).
 */
public interface ConsoleFactsRepository {

	/**
	 * 하루치 사실. {@code date} 가 null 이면 원장이 아는 <b>가장 최근 날</b>이다 — 런이 있던
	 * 마지막 날과 계획만 있던 마지막 날 중 뒤쪽이고, <b>거래일이라는 보장은 없다</b>.
	 *
	 * <p>축이 붙으면 그 조회들이 <b>한 스냅샷에서</b> 돌아야 한다 — 런을 읽은 뒤 writer 가
	 * 커밋하고 작업을 읽으면 "런은 SUCCEEDED 인데 작업은 PENDING" 같은, 어느 시점에도 존재하지
	 * 않은 조합이 한 판정 위에 조립된다(드릴다운 네 조회와 같은 이유).
	 */
	ConsoleFacts facts(LocalDate date);

	/** {@code today} 는 실제로 조회한 날 — 요청이 생략됐을 때 무엇을 봤는지 화면이 알아야 한다. */
	record ConsoleFacts(LocalDate today, OffsetDateTime dbNow, List<RunRow> runs) {
	}

	/**
	 * 런 하나. {@code runKey} 가 곧 사건 식별자의 대상 축이다 — 내부 {@code pipeline_run_id} 가
	 * 아니라 이 값이라야 다른 축(작업 등)이 붙을 때 조인이 선다.
	 */
	record RunRow(String runKey, String lane, LocalDate tradingDate, String ledgerStatus,
			OffsetDateTime ledgerUpdated, OffsetDateTime deadline) {
	}
}
