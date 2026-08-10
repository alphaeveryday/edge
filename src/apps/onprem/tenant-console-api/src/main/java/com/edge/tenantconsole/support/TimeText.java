package com.edge.tenantconsole.support;

import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;

/**
 * 콘솔 표시용 시각 문구 — 원장 시각(TIMESTAMPTZ)을 KST 로 통일해 UI 계약 문자열로 만든다
 * (시장별 현지시각은 원장에 보존돼 있지 않다, AnalysisResponse 와 동일 규율). explanations
 * 목록·상세·반입 상태가 receivedAt/receivedRelative/근거 시각을 미리 포맷된 문자열로 받는
 * mock 계약을 유지하기 위해 서버가 만든다.
 */
public final class TimeText {

	private static final ZoneId KST = ZoneId.of("Asia/Seoul");
	private static final DateTimeFormatter ABS = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm 'KST'");
	private static final DateTimeFormatter DOC = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm 'KST'");

	private TimeText() {
	}

	/** "2026-07-11 10:42 KST" — 반입 절대 시각 표시. */
	public static String absolute(OffsetDateTime at) {
		return ABS.format(at.atZoneSameInstant(KST));
	}

	/** "2026-07-14 09:00 KST" — 근거 문서 시각 표시(검수 상세 kstMinute 와 동일 모양, ALPHA-922). NULL 은 "—". */
	public static String doc(OffsetDateTime at) {
		return at == null ? "—" : DOC.format(at.atZoneSameInstant(KST));
	}

	/** "9분 전"·"3시간 전"·"2일 전" — 반입 상대 시각. 미래·방금은 "방금 전". */
	public static String relative(OffsetDateTime at) {
		return relativeTo(at, OffsetDateTime.now());
	}

	/** now 를 주입받는 결정적 형태 — 표시 규칙 테스트가 경계값을 고정해 검증한다. */
	static String relativeTo(OffsetDateTime at, OffsetDateTime now) {
		long minutes = Duration.between(at, now).toMinutes();
		if (minutes < 1) {
			return "방금 전";
		}
		if (minutes < 60) {
			return minutes + "분 전";
		}
		long hours = minutes / 60;
		if (hours < 24) {
			return hours + "시간 전";
		}
		return (hours / 24) + "일 전";
	}
}
