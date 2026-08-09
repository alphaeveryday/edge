package com.edge.superadmin.service;

import com.edge.common.exception.GeneralException;
import com.edge.superadmin.dto.ConsoleFactsResponse;
import com.edge.superadmin.dto.ConsoleFactsResponse.BoundaryResponse;
import com.edge.superadmin.dto.ConsoleFactsResponse.DatasetResponse;
import com.edge.superadmin.dto.ConsoleFactsResponse.MetaResponse;
import com.edge.superadmin.dto.ConsoleFactsResponse.OutputResponse;
import com.edge.superadmin.dto.ConsoleFactsResponse.RunResponse;
import com.edge.superadmin.dto.ConsoleFactsResponse.TaskResponse;
import com.edge.superadmin.error.AdminErrorStatus;
import com.edge.superadmin.repository.ConsoleFactsRepository;
import com.edge.superadmin.repository.ConsoleFactsRepository.ConsoleFacts;
import com.edge.superadmin.repository.ConsoleFactsRepository.TaskRow;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 콘솔 사실 응답 조립(ALPHA-738).
 *
 * <p>여기서 위반을 판정하지 않는다 — 규칙은 프론트의 순수 함수다. 이 서비스가 하는 유일한 파생은
 * <b>데이터셋 축</b>이고, 그건 계약이 정한 것이다({@code dataset_contract} 테이블이 없어 작업의
 * 컬럼을 dataset 으로 묶는다).
 */
@Service
public class ConsoleFactsService {

	private static final Logger log = LoggerFactory.getLogger(ConsoleFactsService.class);

	private static final ZoneId KST = ZoneId.of("Asia/Seoul");

	/** 계약이 아예 안 걸린 데이터셋 — FRESH/STALE 을 가릴 기준 자체가 없다. */
	private static final String CONTRACT_NOT_APPLIED = "CONTRACT_NOT_APPLIED";

	/** 계약은 있는데 as-of 근거가 없고 원장이 사유도 안 남긴 경우. */
	private static final String ACTUAL_AS_OF_MISSING = "ACTUAL_AS_OF_MISSING";

	private final ConsoleFactsRepository facts;

	public ConsoleFactsService(ConsoleFactsRepository facts) {
		this.facts = facts;
	}

	/**
	 * @param date KST 날짜. 생략하면 원장이 아는 가장 최근 날이고, 응답의 {@code meta.today} 가
	 *             무엇을 봤는지 되돌려준다.
	 * @throws GeneralException 날짜 형식이 틀리거나 <b>미래</b>면 400 ({@link #parseDateParam})
	 */
	public ConsoleFactsResponse facts(String date) {
		ConsoleFacts f = facts.facts(date == null ? null : parseDateParam(date));
		return new ConsoleFactsResponse(
				f.runs().stream().map(RunResponse::from).toList(),
				f.tasks().stream().map(TaskResponse::from).toList(),
				datasets(f.tasks()),
				f.outputs().stream().map(OutputResponse::from).toList(),
				BoundaryResponse.from(f.boundary()),
				new MetaResponse(f.dbNow().toString(), f.today().toString()));
	}

	/**
	 * 작업을 dataset 으로 묶는다. 데이터셋이 없는 작업(순수 조립 스텝 등)은 축 자체가 없어 뺀다.
	 *
	 * <p>{@code unverifiable} 의 술어는 <b>판정 가능성(계약 있음 ∧ actual 근거 있음)의 여집합보다
	 * 한 겹 넓다</b> — 원장이 {@code freshness_status='UNKNOWN'} 이라고 말한 경우가 더 붙는다.
	 * 좁히는 쪽으로 새면 나쁘다: 판정도 못 하고 판정 불가로도 안 잡히는 데이터셋은 화면에서
	 * <b>정상</b>으로 보인다. 넓히는 쪽 대가는 R08 과의 겹침뿐이고 계약 문서에 적혀 있다.
	 */
	private static List<DatasetResponse> datasets(List<TaskRow> tasks) {
		/* 빈 문자열도 축이 아니다. 그대로 내리면 id 가 빈 데이터셋이 서고, R09 가 그걸 위반으로
		 * 만들면 엔진의 빈 대상 가드가 **R09 전체**를 못 돎(identity)으로 세워 다른 데이터셋의
		 * 판정 불가까지 같이 버린다. 다만 **조용히 빼지는 않는다**(Rule 12) — 스키마에 비공백
		 * 제약이 없어 원장에 실제로 들어올 수 있고, 그건 writer 결함이다. 그 작업 자체는
		 * `tasks[]` 에 그대로 남아 작업 축 규칙(R05~R07)은 계속 본다. */
		List<String> blank = tasks.stream()
				.filter(t -> t.dataset() != null && t.dataset().isBlank())
				.map(TaskRow::taskKey).toList();
		/* ⚠️ 경고는 **요청당 한 줄**로 접는다. 콘솔은 60초마다 재조회하고 운영자가 여럿이라,
		 * 행마다 찍으면 원장 결함 하나가 로그량을 요청량 × 작업 수로 증폭한다. 억제 캐시를 두지
		 * 않은 것은 의도다 — 조용해지면 남는 신호가 없고, 원장이 고쳐지면 저절로 멎는다. */
		if (!blank.isEmpty()) {
			log.warn("dataset 이 빈 작업 {}건을 데이터셋 축에서 제외한다: {} — 원장 writer 결함일 수 있다",
					blank.size(), blank);
		}

		Map<String, List<TaskRow>> byDataset = new LinkedHashMap<>();
		tasks.stream()
				.filter(t -> t.dataset() != null && !t.dataset().isBlank())
				.sorted(Comparator.comparing(TaskRow::dataset))
				.forEach(t -> byDataset.computeIfAbsent(t.dataset(), k -> new java.util.ArrayList<>())
						.add(t));

		return byDataset.entrySet().stream().map(e -> {
			List<TaskRow> rows = e.getValue();
			boolean contract = rows.stream().anyMatch(t -> t.datasetContractKey() != null);
			/* 🔴 **as-of 쌍은 한 작업에서 통째로 가져온다.** 한 데이터셋에 작업이 여럿일 때
			 * `expected` 와 `actual` 을 각자 접으면(`max` 와 `min`) **어느 작업에도 없던 쌍**이
			 * 만들어져 둘 다 FRESH 인 작업 두 개가 거짓 STALE 을 낸다(리뷰 4라운드 반례:
			 * 01/01 과 02/02 → expected 02 · actual 01). 기준은 **가장 오래된 근거를 가진 작업**이다 —
			 * 하나만 최신이어도 전체가 최신으로 보이면 R08 이 조용해진다.
			 * as-of 근거가 아예 없으면 비교할 쌍이 없으므로 expected 만 표시용으로 접는다. */
			/* 동률이면 **기대일이 늦은 쪽**을 고른다 — `actual < expected` 가 성립할 가능성이 큰 쪽,
			 * 곧 R08 이 STALE 을 드러내는 방향이다. 마지막 tie-break 는 작업 키다: 안 두면 같은
			 * 원장이 조회 순서에 따라 FRESH 로도 STALE 로도 판정된다(리뷰 5라운드). */
			TaskRow stalest = rows.stream().filter(t -> t.actualAsOf() != null)
					.min(Comparator.comparing(TaskRow::actualAsOf)
							.thenComparing(TaskRow::expectedAsOf,
									Comparator.nullsLast(Comparator.reverseOrder()))
							.thenComparing(TaskRow::taskKey))
					.orElse(null);
			LocalDate actualAsOf = stalest == null ? null : stalest.actualAsOf();
			LocalDate expectedAsOf = stalest != null
					? stalest.expectedAsOf()
					: maxDate(rows.stream().map(TaskRow::expectedAsOf));
			OffsetDateTime collectedAt = rows.stream().map(TaskRow::collectedAt)
					.filter(java.util.Objects::nonNull).max(Comparator.naturalOrder()).orElse(null);

			/* 🔴 **원장이 UNKNOWN 이라고 말했으면 그게 답이다.** `actual_as_of_date` 유무만 보면
			 * 스키마가 허용하는 조합(`freshness_status='UNKNOWN'` + actual 존재 —
			 * `ck_ops_expected_task_freshness_pair` 는 그 둘을 안 묶는다)이 **판정 가능**으로 서고,
			 * 값이 우연히 기대일과 같으면 R08 도 조용해 두 규칙 다 위반 0 이 된다(리뷰가 잡았다).
			 * 사유는 **UNKNOWN 인 그 작업**에서 꺼낸다 — 전체에서 첫 사유를 집으면 판정 불가인데
			 * 멀쩡한 작업의 `AS_OF_MATCH` 를 사유로 다는, 서로 다른 작업을 한 사실로 섞는 일이 된다. */
			String unknownReason = rows.stream()
					.filter(t -> "UNKNOWN".equals(t.freshnessStatus()))
					.map(TaskRow::freshnessReason).filter(java.util.Objects::nonNull)
					.findFirst().orElse(null);
			boolean ledgerSaysUnknown = rows.stream()
					.anyMatch(t -> "UNKNOWN".equals(t.freshnessStatus()));
			String unverifiable = null;
			if (!contract) {
				unverifiable = CONTRACT_NOT_APPLIED;
			} else if (ledgerSaysUnknown) {
				unverifiable = unknownReason != null ? unknownReason : ACTUAL_AS_OF_MISSING;
			} else if (actualAsOf == null) {
				/* 상태가 UNKNOWN 이 아닌데 actual 이 없다 — 스키마상 사유도 없다
				 * (`ck_..._freshness_pair`: 사유는 상태가 있을 때만 존재한다). */
				unverifiable = ACTUAL_AS_OF_MISSING;
			}
			return new DatasetResponse(e.getKey(), contract,
					expectedAsOf == null ? null : expectedAsOf.toString(),
					actualAsOf == null ? null : actualAsOf.toString(),
					collectedAt == null ? null : collectedAt.toString(),
					unverifiable);
		}).toList();
	}

	private static LocalDate maxDate(java.util.stream.Stream<LocalDate> dates) {
		return dates.filter(java.util.Objects::nonNull).max(Comparator.naturalOrder()).orElse(null);
	}

	/**
	 * KST 날짜 파라미터 파서 — {@code SourceService} 와 같은 규약(확장 연도도 400). 오타가 아래
	 * 계층에서 터져 500 으로 위장되면 운영자가 원인을 못 찾는다.
	 *
	 * <p><b>미래 날짜도 400 이다.</b> 아직 오지 않은 날의 산출은 <b>실측 0 이 아니라 "아직"</b>인데,
	 * 이 응답에는 그 둘을 가르는 자리가 없다({@code outputs[].today} 는 수 하나다). 그대로 내리면
	 * R13 이 다섯 산출을 전부 −100% 로 판정해 거짓 P1 을 만든다.
	 *
	 * <p>⚠️ 상한은 <b>KST 오늘</b>이지 원장의 최신 거래일이 아니다. 최신 거래일로 자르면 <b>계획이
	 * 통째로 안 돈 날</b>(런 0건 + PLANNER_MISSING)을 조회할 수 없게 되는데, 그날이 바로 R01 이
	 * P0 를 내야 하는 날이다 — 게이트가 사고를 숨기는 방향으로 서면 안 된다.
	 */
	private static LocalDate parseDateParam(String date) {
		LocalDate parsed;
		try {
			parsed = LocalDate.parse(date);
		} catch (java.time.format.DateTimeParseException e) {
			throw new GeneralException(AdminErrorStatus.INVALID_REQUEST);
		}
		if (parsed.getYear() < 1 || parsed.getYear() > 9999) {
			throw new GeneralException(AdminErrorStatus.INVALID_REQUEST);
		}
		if (parsed.isAfter(LocalDate.now(KST))) {
			throw new GeneralException(AdminErrorStatus.INVALID_REQUEST);
		}
		return parsed;
	}
}
