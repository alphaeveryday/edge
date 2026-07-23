package com.edge.superadmin.mock;

import org.springframework.stereotype.Component;

import java.util.List;

/**
 * sources 도메인 mock 데이터 — super-admin-ui 시안(v0.1) 목데이터 이식(ALPHA-515).
 * 읽기 전용 표면이라 불변이다. DB(파이프라인 상태) 연동 시 service 의 이 스토어
 * 호출부를 수집 상태 조회로 교체한다.
 */
@Component
public class SourceMockStore {

	public record DataSource(String name, String provider, String status, String lastCollected,
			String volume) {
	}

	public record SourceReport(String checkedAt, List<DataSource> sources) {
	}

	private final SourceReport report = new SourceReport("2026-07-12 10:20 KST", List.of(
			new DataSource("뉴스", "연합인포맥스 · 뉴스핌 · Reuters 외 12개 매체", "COLLECTING",
					"1분 전", "8,214건"),
			new DataSource("공시", "DART · KIND", "COLLECTING", "4분 전", "342건"),
			new DataSource("시세", "KRX 시세 · NASDAQ TotalView", "COLLECTING",
					"실시간 (지연 < 1s)", "1,284만 틱"),
			new DataSource("실적 / 재무", "DART 재무 API · SEC EDGAR", "DELAYED",
					"3시간 전", "118건"),
			new DataSource("ETF 구성 종목", "운용사 PDF (KODEX · TIGER 외 34개사)", "COLLECTING",
					"오늘 07:30", "912개 ETF")));

	public SourceReport report() {
		return report;
	}
}
