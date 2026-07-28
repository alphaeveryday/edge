package com.edge.superadmin.dto;

import com.edge.superadmin.repository.AnalysisRepository.AnalysisRow;
import com.edge.superadmin.repository.AnalysisRepository.EvidenceRow;

import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.List;

/**
 * 가격 변동 분석 응답 — 필드는 super-admin-ui analyses 타입과 동일한 camelCase(ALPHA-601).
 * 원장(explanation_*)의 판정을 UI 어휘로 <b>번역만</b> 한다: {@code run_status}→status,
 * {@code confidence_level}→confidence. 새 판정을 만들지 않는다(SourceService 원칙).
 *
 * <p>{@code corrected} 는 항상 false — 정정 이력이 원장에 아직 없다. 쓰기 전환(ALPHA-602)
 * 에서 저장 위치가 생기면 함께 실값이 된다. EXCLUDED 도 같은 이유로 여기서는 도달 불가다.
 */
public record AnalysisResponse(String id, String name, String code, String market, int direction,
		double changePct, String status, String basisTime, String basisTimeAbs, String doneTime,
		String confidence, boolean corrected, String result, List<EvidenceResponse> evidence) {

	/** 표시는 KST 로 통일한다 — 원장 시각은 TIMESTAMPTZ 라 시장별 현지시각은 보존돼 있지 않다. */
	private static final ZoneId KST = ZoneId.of("Asia/Seoul");
	private static final DateTimeFormatter SHORT_TIME = DateTimeFormatter.ofPattern("MM-dd HH:mm");
	private static final DateTimeFormatter ABS_TIME =
			DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm 'KST'");
	private static final DateTimeFormatter DOC_TIME = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm");

	public record EvidenceResponse(String type, String title, String source, String time) {

		public static EvidenceResponse from(EvidenceRow e) {
			// document_type 은 CHECK 로 NEWS|DISCLOSURE 뿐 — 새 값이 생기면 라벨 없이
			// 코드가 그대로 노출된다(숨기는 것보다 낫다).
			String type = switch (e.documentType()) {
				case "NEWS" -> "뉴스";
				case "DISCLOSURE" -> "공시";
				default -> e.documentType();
			};
			// document.title 은 NULL 허용 — UI 계약(title: string)을 깨지 않게 표시 폴백을 준다
			return new EvidenceResponse(type, e.title() == null ? "(제목 없음)" : e.title(),
					e.sourceCode(),
					e.publishedAt() == null ? "—" : format(DOC_TIME, e.publishedAt()));
		}
	}

	public static AnalysisResponse from(AnalysisRow row) {
		String status = uiStatus(row.runStatus());
		return new AnalysisResponse(
				row.runId(),
				row.etfName(),
				row.ticker(),
				uiMarket(row.marketCode()),
				row.observedReturn() < 0 ? -1 : 1,
				Math.abs(row.observedReturn()) * 100,
				status,
				format(SHORT_TIME, row.detectedAt()),
				format(ABS_TIME, row.detectedAt()),
				doneTime(status, row.finishedAt()),
				row.confidenceLevel(),
				false,
				result(status, row.summary()),
				row.evidence().stream().map(EvidenceResponse::from).toList());
	}

	private static String uiStatus(String runStatus) {
		return switch (runStatus) {
			case "SUCCEEDED" -> "COMPLETED";
			case "PENDING", "RUNNING" -> "PENDING";
			case "FAILED", "CANCELLED" -> "FAILED";
			// CHECK 밖의 새 상태는 그대로 노출 — 조용히 한 축으로 뭉개면 원장의 새 어휘가
			// 화면에서 다른 뜻으로 둔갑한다(Rule 12).
			default -> runStatus;
		};
	}

	/** MIC(instrument.market_code)→UI 시장 구분. 미지 MIC 는 그대로 노출한다. */
	private static String uiMarket(String mic) {
		return switch (mic) {
			case "XKRX", "XKOS", "XKON" -> "KRX";
			case "XNAS", "XNGS", "XNMS" -> "NASDAQ";
			default -> mic;
		};
	}

	private static String doneTime(String status, OffsetDateTime finishedAt) {
		// FAILED 도 finished_at 이 있지만 "분석 완료 시각"은 완료 건에만 뜻이 있다.
		return "COMPLETED".equals(status) && finishedAt != null
				? format(ABS_TIME, finishedAt) : "—";
	}

	private static String result(String status, String summary) {
		if (summary != null) {
			return summary;
		}
		return switch (status) {
			case "PENDING" -> "분석 대기 중입니다. 근거 데이터 수집이 완료되면 자동으로 분석이 시작됩니다.";
			case "FAILED" -> "분석이 실패했습니다. 실행 상세는 파이프라인 실행 이력에서 확인할 수 있습니다.";
			// SUCCEEDED 런에 결과 행이 없는 건 스키마가 막지 않는 원장 불일치다 — 빈 문자열로
			// 완료처럼 두면 운영자가 못 본다(Rule 12).
			case "COMPLETED" -> "설명 결과가 원장에 없습니다 — 완료 런에 explanation_result 가 없는 원장 불일치입니다.";
			default -> "설명 결과가 원장에 없습니다.";
		};
	}

	private static String format(DateTimeFormatter fmt, OffsetDateTime at) {
		return fmt.format(at.atZoneSameInstant(KST));
	}
}
