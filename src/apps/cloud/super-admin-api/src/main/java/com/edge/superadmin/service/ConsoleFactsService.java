package com.edge.superadmin.service;

import com.edge.common.exception.GeneralException;
import com.edge.superadmin.dto.ConsoleFactsResponse;
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
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.TreeMap;
import java.util.stream.Collectors;
import java.util.stream.Stream;

/**
 * 콘솔 사실 응답 조립(ALPHA-738).
 *
 * <p>여기서 위반을 판정하지 않는다 — 규칙은 프론트의 순수 함수다. 나머지 축에서 이 서비스가 하는
 * 일은 원장 행을 <b>와이어 형으로 옮기는 것</b>(1:1)뿐이고, <b>여러 행을 하나로 접는 축은
 * 데이터셋 축뿐</b>이다({@link #datasets}) — {@code dataset_contract} 테이블이 없어 작업의 컬럼을
 * {@code dataset} 으로 묶는 것 말고는 그 축을 세울 방법이 없다.
 *
 * <p>⚠️ "서버가 파생을 안 한다"는 뜻은 아니다 — 런 축의 계획 결손 슬롯은 리포지토리가
 * {@code scope_key} 를 파싱해 <b>행을 합성한다</b>. 다만 그건 슬롯 하나가 행 하나라 접기가 없다.
 */
@Service
public class ConsoleFactsService {

	private static final Logger log = LoggerFactory.getLogger(ConsoleFactsService.class);

	private static final ZoneId KST = ZoneId.of("Asia/Seoul");

	/** 계약이 아예 안 걸린 데이터셋 — FRESH/STALE 을 가릴 기준 자체가 없다. */
	private static final String CONTRACT_NOT_APPLIED = "CONTRACT_NOT_APPLIED";

	/** 계약은 있는데 actual as-of 근거 자체가 없는 경우. */
	private static final String ACTUAL_AS_OF_MISSING = "ACTUAL_AS_OF_MISSING";

	/**
	 * 원장이 {@code UNKNOWN} 이라고 말했는데 <b>사유를 안 남긴</b> 경우.
	 *
	 * <p>{@link #ACTUAL_AS_OF_MISSING} 을 돌려쓰지 않는다 — UNKNOWN 은 as-of 가 <b>있어도</b> 설 수
	 * 있어서({@code ck_ops_expected_task_verified_as_of} 는 {@code actual > expected} 인 UNKNOWN 을
	 * 통과시킨다), 그 경우 응답이 actual 날짜를 실은 채 "as-of 가 없다"고 말하게 된다. 판정 불가는
	 * 맞지만 <b>사유가 거짓</b>이 되고, 운영자는 없는 결손을 찾으러 간다.
	 */
	private static final String FRESHNESS_REASON_MISSING = "FRESHNESS_REASON_MISSING";

	/**
	 * 계약은 걸렸는데 <b>그 계약으로 그 날 실행 대상이던 작업이 없는</b> 데이터셋.
	 *
	 * <p>Planner 는 계약 키를 {@code plan_status} 와 무관하게 쓰고, 신선도는 {@code DUE} 일 때만
	 * 쓴다({@code ops/planner.py}). 마이그레이션이 정의한 대로 그 NULL 은 <b>NOT_APPLICABLE 이고
	 * UNKNOWN 과 다르다</b>({@code V202607311300__add_expected_task_freshness.sql}). 그래서 그
	 * 데이터셋을 {@link #ACTUAL_AS_OF_MISSING} 으로 접으면 아무도 기대하지 않은 데이터를 안 왔다고
	 * 세게 된다.
	 *
	 * <p>⚠️ <b>오늘 이 코드가 나가는 원장 조합은 없다</b> — 계약이 걸린 카탈로그 엔트리는
	 * {@code ETF_HOLDINGS_COLLECTION_KRX} 하나뿐이고 그 엔트리는 {@code kr_trading_calendar} 를
	 * 켜지 않아 휴장일에도 {@code DUE} 로 선다({@code ops/catalog.py}). 그 플래그를 켠 다섯 엔트리는
	 * 계약이 없다. 즉 <b>휴장일 때문에 이 분기가 필요한 것이 아니다</b>(리뷰 4라운드에서 그렇게
	 * 적었다가 정정했다 — 계약과 달력 게이트가 겹치는 엔트리가 아직 없다). 남겨 두는 이유는
	 * 스키마와 Planner 가 그 조합을 <b>허용</b>하고, 계약 엔트리 하나에 그 플래그가 붙는 순간
	 * 대안이 조용한 거짓 경보이기 때문이다.
	 */
	private static final String NOT_APPLICABLE = "NOT_APPLICABLE";

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
				new MetaResponse(f.dbNow().toString(), f.today().toString()));
	}

	/**
	 * 작업을 {@code dataset} 으로 묶는다 — 정렬은 데이터셋 id 다({@link TreeMap}). 순서를 안
	 * 고정하면 같은 원장이 조회마다 다른 순서로 나가고, 소비자가 "첫 데이터셋"을 집는 순간 판정이
	 * 흔들린다(런 축이 {@code run_key} 로 고정한 것과 같은 이유).
	 *
	 * <p>데이터셋이 없는 작업(순수 조립 스텝 등)은 축 자체가 없어 뺀다.
	 */
	private static List<DatasetResponse> datasets(List<TaskRow> tasks) {
		/* 빈 문자열도 축이 아니다. 그대로 내리면 id 가 빈 데이터셋이 서고, 그걸 위반으로 만드는
		 * 규칙은 빈 대상 가드에 걸려 **그 규칙 전체**를 못 돎(identity)으로 세운다 — 다른
		 * 데이터셋의 판정 불가까지 같이 버려진다. 다만 **조용히 빼지는 않는다**(Rule 12): 스키마에
		 * 비공백 제약이 없어 원장에 실제로 들어올 수 있고, 그건 writer 결함이다. 그 작업 자체는
		 * `tasks[]` 에 그대로 남아 작업 축 규칙은 계속 본다.
		 *
		 * ⚠️ 경고는 **요청당 한 줄**로 접는다. 콘솔은 주기적으로 재조회하고 운영자가 여럿이라,
		 * 행마다 찍으면 원장 결함 하나가 로그량을 요청량 × 작업 수로 증폭한다. 억제 캐시를 두지
		 * 않은 것은 의도다 — 조용해지면 남는 신호가 없고, 원장이 고쳐지면 저절로 멎는다. */
		List<String> blank = tasks.stream()
				.filter(t -> t.dataset() != null && t.dataset().isBlank())
				.map(TaskRow::taskKey).toList();
		if (!blank.isEmpty()) {
			log.warn("dataset 이 빈 작업 {}건을 데이터셋 축에서 제외한다: {} — 원장 writer 결함일 수 있다",
					blank.size(), blank);
		}

		Map<String, List<TaskRow>> byDataset = tasks.stream()
				.filter(t -> t.dataset() != null && !t.dataset().isBlank())
				.collect(Collectors.groupingBy(TaskRow::dataset, TreeMap::new, Collectors.toList()));
		return byDataset.entrySet().stream()
				.map(e -> dataset(e.getKey(), e.getValue()))
				.toList();
	}

	/**
	 * 한 데이터셋의 사실을 그 데이터셋의 작업들에서 접는다.
	 *
	 * <p>{@code unverifiable} 의 술어는 <b>판정 가능성(계약 있음 ∧ actual 근거 있음)의 여집합보다
	 * 한 겹 넓다</b> — 원장이 {@code freshness_status='UNKNOWN'} 이라고 말한 경우가 더 붙는다.
	 * 좁히는 쪽으로 새면 나쁘다: 판정도 못 하고 판정 불가로도 안 잡히는 데이터셋은 화면에서
	 * <b>정상</b>으로 보인다. 넓히는 쪽 대가는 신선도 규칙과의 겹침뿐이고 계약 문서에 적혀 있다.
	 */
	private static DatasetResponse dataset(String id, List<TaskRow> rows) {
		boolean contract = rows.stream().anyMatch(ConsoleFactsService::hasContract);

		/* 🔴 **신선도는 계약이 걸린 채 그 날 실행 대상이던 작업에서만 접는다**(`DUE` ∧ 계약 키 있음).
		 * 이 축의 사실은 **계약의 것**이지 데이터셋에 붙은 아무 작업의 것이 아니다.
		 *
		 * 네 값 중 셋(actual as-of·수집 시각·신선도)은 스키마가 이미 그렇게 강제한다 —
		 * `ck_ops_expected_task_freshness_applicability` 가 `DUE` ∧ 계약이 아닌 행의 그 컬럼들을
		 * 전부 NULL 로 묶는다. 좁히지 않아도 결과가 같다는 뜻이다.
		 *
		 * ⚠️ **`expected_as_of_date` 하나만 다르다** — Planner 는 그 컬럼을 계약 유무와 무관하게
		 * **모든 작업에** 쓰고, 값이 갈린다(계약 작업은 `resolve_expected_as_of`, 나머지는
		 * `slot_date` — `ops/planner.py`). 그래서 안 좁히면 계약이 없는 작업의 기대일이 계약
		 * 데이터셋의 `expectedAsOf` 로 나갈 수 있다. 실물 조합이다: `etf_holdings` 는 계약이 걸린
		 * `ETF_HOLDINGS_COLLECTION_KRX` 와 계약이 없는 `NORMALIZE_ETF` 를 함께 갖고
		 * (`ops/catalog.py`), 그 계약은 actual 을 늘 NULL 로 써서(ADR-0043 첫 슬라이스) 기대일
		 * 접기가 **정상 경로**다 — 아무 계약도 하지 않은 기대일에 계약 작업의 사유가 붙는다. */
		List<TaskRow> judged = rows.stream()
				.filter(t -> "DUE".equals(t.planStatus()) && hasContract(t))
				.toList();

		/* 🔴 **as-of 쌍은 한 작업에서 통째로 가져온다.** 한 데이터셋에 작업이 여럿일 때
		 * `expected` 와 `actual` 을 각자 접으면(`max` 와 `min`) **어느 작업에도 없던 쌍**이
		 * 만들어져, 둘 다 FRESH 인 작업 두 개가 거짓 STALE 을 낸다(01/01 과 03/03 → expected 03 ·
		 * actual 01). 기준은 **가장 오래된 근거를 가진 작업**이다 — 하나만 최신이어도 전체가
		 * 최신으로 보이면 낡음이 조용해진다.
		 *
		 * 동률이면 **기대일이 늦은 쪽**을 고른다: `actual < expected` 가 성립할 가능성이 큰 쪽,
		 * 곧 낡음을 드러내는 방향이다. 마지막 tie-break 는 작업 키다 — as-of 쌍이 완전히 동률이면
		 * 어느 행을 골라도 쌍은 같지만, **판정 불가 사유가 이 선택을 따라오므로**(아래) 안 두면
		 * 같은 원장이 조회 순서에 따라 서로 다른 사유를 낸다. */
		TaskRow stalest = judged.stream().filter(t -> t.actualAsOf() != null)
				.min(Comparator.comparing(TaskRow::actualAsOf)
						.thenComparing(TaskRow::expectedAsOf,
								Comparator.nullsLast(Comparator.reverseOrder()))
						.thenComparing(TaskRow::taskKey))
				.orElse(null);
		/* as-of 근거가 아예 없으면 비교할 쌍이 없다 — 그때만 expected 를 따로 접는다(표시용). */
		LocalDate actualAsOf = stalest == null ? null : stalest.actualAsOf();
		LocalDate expectedAsOf = stalest != null ? stalest.expectedAsOf()
				: judged.stream().map(TaskRow::expectedAsOf).filter(Objects::nonNull)
						.max(Comparator.naturalOrder()).orElse(null);
		OffsetDateTime collectedAt = judged.stream().map(TaskRow::collectedAt)
				.filter(Objects::nonNull).max(Comparator.naturalOrder()).orElse(null);

		/* 🔴 **원장이 UNKNOWN 이라고 말했으면 그게 답이다.** `actual_as_of_date` 유무만 보면
		 * 스키마가 허용하는 조합(`freshness_status='UNKNOWN'` + actual 존재 —
		 * `ck_ops_expected_task_verified_as_of` 는 `actual > expected` 인 UNKNOWN 을 허용한다)이
		 * **판정 가능**으로 서고, 값이 우연히 기대일과 같으면 낡음 규칙도 조용해 두 규칙 다 위반
		 * 0 이 된다.
		 *
		 * 사유는 **as-of 쌍을 준 그 작업**에서 먼저 찾는다. 전체에서 첫 사유를 집으면 판정 불가인데
		 * 멀쩡한 작업의 `AS_OF_MATCH` 를 사유로 다는 일이 되고, UNKNOWN 인 작업이 **둘 이상**일 때는
		 * B 의 as-of 와 A 의 사유가 한 행에 실려 서로 다른 작업이 한 사실로 섞인다(리뷰가 잡았다 —
		 * 그 조합은 스키마가 허용한다: UNKNOWN 은 `actual > expected` 여도 성립한다). 기준 작업이
		 * UNKNOWN 이 아닐 때만 나머지에서 찾는다. */
		Optional<TaskRow> unknown = Stream.concat(Stream.ofNullable(stalest), judged.stream())
				.filter(t -> "UNKNOWN".equals(t.freshnessStatus())).findFirst();

		String unverifiable;
		if (!contract) {
			unverifiable = CONTRACT_NOT_APPLIED;
		} else if (judged.isEmpty()) {
			// 계약은 걸렸는데 그 계약으로 그 날 실행 대상이던 작업이 없었다(위 `judged` 주석).
			unverifiable = NOT_APPLICABLE;
		} else if (unknown.isPresent()) {
			/* 스키마상 UNKNOWN 이면 사유가 있지만(`ck_ops_expected_task_freshness_pair`) 그 제약은
			 * `IS NOT NULL` 이라 **빈 문자열을 막지 않는다**. 빈 사유를 그대로 내면 판정 코드를
			 * truthy 로 보는 소비자가 판정 불가 데이터셋을 정상으로 건너뛴다 — 이 축이 없애려는
			 * 바로 그 실패다. 판정 불가는 유지하고 사유만 기본 코드로 떨어뜨린다. */
			unverifiable = unknown.map(TaskRow::freshnessReason)
					.filter(reason -> !reason.isBlank())
					.orElse(FRESHNESS_REASON_MISSING);
		} else if (actualAsOf == null) {
			/* ⚠️ **스키마상 여기 오지 않는다.** `judged` 는 계약 ∧ `DUE` 라 상태가 non-null 이고
			 * (`ck_ops_expected_task_contract_freshness_status`), FRESH·STALE 은 actual 을 요구하며
			 * (`ck_ops_expected_task_verified_as_of`), UNKNOWN 은 위 분기가 잡는다.
			 *
			 * 그래도 `null` 로 떨어뜨리지 않는다 — 원장이 그 제약을 잃는 날 이 자리의 대안은
			 * **판정 불가가 조용히 정상으로 서는 것**이고, 그게 이 축이 없애려는 실패다(Rule 12).
			 * 스키마가 막는 상태에 새 가드를 더하지는 않되, 이미 갈래가 있는 자리를 비우지도 않는다. */
			unverifiable = ACTUAL_AS_OF_MISSING;
		} else {
			unverifiable = null;
		}

		return new DatasetResponse(id, contract,
				expectedAsOf == null ? null : expectedAsOf.toString(),
				actualAsOf == null ? null : actualAsOf.toString(),
				collectedAt == null ? null : collectedAt.toString(),
				unverifiable);
	}

	/**
	 * 계약이 <b>실제로</b> 걸렸나. {@code null} 만 보지 않는다 — 스키마는 계약 3종을 함께
	 * non-null 로 묶을 뿐({@code ck_ops_expected_task_contract_snapshot}) <b>비공백 제약이 없어</b>
	 * 빈 문자열 키가 들어올 수 있다. 그걸 계약으로 세면 등록될 수 없는 식별자가 "계약 적용됨"으로
	 * 서고, actual 만 있으면 그 데이터셋이 <b>판정 가능</b>으로 조용히 인증된다.
	 *
	 * <p>같은 규칙을 이 파일이 두 군데서 이미 쓴다 — 빈 {@code dataset} 은 축에서 빼고, 빈
	 * {@code freshness_reason} 은 사유로 안 친다. <b>빈 문자열은 값이 아니다.</b>
	 */
	private static boolean hasContract(TaskRow t) {
		return t.datasetContractKey() != null && !t.datasetContractKey().isBlank();
	}

	/**
	 * KST 날짜 파라미터 파서 — {@code SourceService} 와 같은 규약(확장 연도도 400). 오타가 아래
	 * 계층에서 터져 500 으로 위장되면 운영자가 원인을 못 찾는다.
	 *
	 * <p><b>미래 날짜도 400 이다.</b> 아직 오지 않은 날의 사실은 <b>실측 0 이 아니라 "아직"</b>인데,
	 * 이 응답에는 그 둘을 가르는 자리가 없다. 그대로 내리면 뒤에 붙을 산출 축이 전부 −100% 로
	 * 판정돼 거짓 경보가 선다.
	 *
	 * <p>⚠️ 상한은 <b>KST 오늘</b>이지 원장의 최신 거래일이 아니다. 최신 거래일로 자르면 <b>계획이
	 * 통째로 안 돈 날</b>(런 0건 + PLANNER_MISSING)을 조회할 수 없게 되는데, 그날이 바로 콘솔이
	 * 열려야 하는 날이다 — 게이트가 사고를 숨기는 방향으로 서면 안 된다.
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
