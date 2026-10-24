package com.edge.superadmin.service;

import com.edge.common.exception.GeneralException;
import com.edge.superadmin.dto.AnalysisResultDetailResponse;
import com.edge.superadmin.dto.AnalysisResultPageResponse;
import com.edge.superadmin.error.AdminErrorStatus;
import com.edge.superadmin.repository.AnalysisResultRepository;
import com.edge.superadmin.repository.AnalysisResultRepository.Detail;
import com.edge.superadmin.repository.AnalysisResultRepository.Kind;
import com.edge.superadmin.repository.AnalysisResultRepository.ListQuery;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.time.ZoneId;
import java.time.format.DateTimeParseException;
import java.util.Set;

/**
 * 새 분석 엔진의 결과 목록·상세(ALPHA-1333). 요청 값을 검사해 저장소에 넘기기만 한다 — 판정은 없다.
 * 와이어 형은 {@code dto} 의 응답 record 로 옮겨 담는다.
 */
@Service
public class AnalysisResultService {

	private static final ZoneId KST = ZoneId.of("Asia/Seoul");
	private static final Set<String> STATUSES = Set.of("completed", "failed", "running");
	static final int MAX_SIZE = 200;

	private final AnalysisResultRepository results;

	public AnalysisResultService(AnalysisResultRepository results) {
		this.results = results;
	}

	/**
	 * @param from 분석 시각의 KST 날짜 하한(포함). 생략하면 KST 오늘
	 * @param to   KST 날짜 상한(포함). 생략하면 {@code from} 과 같은 날 — 기본 기간은 하루다
	 * @throws GeneralException 종류·날짜·상태·페이지 값이 틀리거나 {@code from > to} 면 400
	 */
	public AnalysisResultPageResponse list(String kind, String from, String to, String etf, String status,
			boolean allSources, int page, int size) {
		LocalDate start = from == null ? LocalDate.now(KST) : parseDate(from);
		LocalDate end = to == null ? start : parseDate(to);
		if (end.isBefore(start) || page < 0 || size < 1 || size > MAX_SIZE
				|| (status != null && !STATUSES.contains(status))) {
			throw new GeneralException(AdminErrorStatus.INVALID_REQUEST);
		}
		return AnalysisResultPageResponse.from(results.list(new ListQuery(parseKind(kind),
				start.atStartOfDay(KST).toOffsetDateTime(),
				end.plusDays(1).atStartOfDay(KST).toOffsetDateTime(),
				etf == null || etf.isBlank() ? null : etf.strip(), status, allSources, page, size)));
	}

	/** @throws GeneralException 그런 분석이 없으면 404 */
	public AnalysisResultDetailResponse detail(String kind, String analysisId) {
		Detail detail = results.detail(parseKind(kind), analysisId);
		if (detail == null) {
			throw new GeneralException(AdminErrorStatus.ANALYSIS_NOT_FOUND);
		}
		return AnalysisResultDetailResponse.from(detail);
	}

	private static Kind parseKind(String kind) {
		return switch (kind == null ? "" : kind) {
			case "outlook" -> Kind.OUTLOOK;
			case "movement" -> Kind.MOVEMENT;
			default -> throw new GeneralException(AdminErrorStatus.INVALID_REQUEST);
		};
	}

	private static LocalDate parseDate(String date) {
		try {
			LocalDate parsed = LocalDate.parse(date);
			if (parsed.getYear() >= 1 && parsed.getYear() <= 9999) {
				return parsed;
			}
		} catch (DateTimeParseException e) {
			// 아래에서 400
		}
		throw new GeneralException(AdminErrorStatus.INVALID_REQUEST);
	}
}
