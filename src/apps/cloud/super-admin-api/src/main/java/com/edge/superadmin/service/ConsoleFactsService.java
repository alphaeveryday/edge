package com.edge.superadmin.service;

import com.edge.common.exception.GeneralException;
import com.edge.superadmin.dto.ConsoleFactsResponse;
import com.edge.superadmin.dto.ConsoleFactsResponse.MetaResponse;
import com.edge.superadmin.dto.ConsoleFactsResponse.RunResponse;
import com.edge.superadmin.dto.ConsoleFactsResponse.TaskResponse;
import com.edge.superadmin.error.AdminErrorStatus;
import com.edge.superadmin.repository.ConsoleFactsRepository;
import com.edge.superadmin.repository.ConsoleFactsRepository.ConsoleFacts;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.time.ZoneId;

/**
 * 콘솔 사실 응답 조립(ALPHA-738).
 *
 * <p>여기서 위반을 판정하지 않는다 — 규칙은 프론트의 순수 함수다. 이 서비스가 하는 일은 조회
 * 창을 정하고(요청한 날을 검사해 넘긴다) 원장 행을 <b>와이어 형으로 옮기는 것</b>뿐이다.
 * 남은 사실 축은 뒤따르는 조각이 하나씩 더한다.
 */
@Service
public class ConsoleFactsService {

	private static final ZoneId KST = ZoneId.of("Asia/Seoul");

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
				new MetaResponse(f.dbNow().toString(), f.today().toString()));
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
