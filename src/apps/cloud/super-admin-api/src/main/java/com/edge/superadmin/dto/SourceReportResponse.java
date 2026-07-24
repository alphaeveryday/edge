package com.edge.superadmin.dto;

import com.edge.superadmin.mock.SourceMockStore.DataSource;
import com.edge.superadmin.mock.SourceMockStore.SourceReport;

import java.util.List;

/**
 * 데이터 소스 수집 상태 응답. 필드는 super-admin-ui sources 타입과 동일한 camelCase.
 * mock 스토어 record(SourceReport/DataSource)와 형식이 같아도 와이어 형은 별도
 * 타입으로 둔다. 중첩 소스는 DataSourceResponse.
 */
public record SourceReportResponse(String checkedAt, List<DataSourceResponse> sources) {

	public record DataSourceResponse(String name, String provider, String status,
			String lastCollected, String volume) {

		public static DataSourceResponse from(DataSource d) {
			return new DataSourceResponse(d.name(), d.provider(), d.status(), d.lastCollected(),
					d.volume());
		}
	}

	public static SourceReportResponse from(SourceReport report) {
		return new SourceReportResponse(report.checkedAt(),
				report.sources().stream().map(DataSourceResponse::from).toList());
	}
}
