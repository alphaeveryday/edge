package com.edge.superadmin.service;

import com.edge.superadmin.mock.SourceMockStore;
import com.edge.superadmin.mock.SourceMockStore.SourceReport;
import org.springframework.stereotype.Service;

/**
 * sources 화면 mock 표면(ALPHA-515) — mock 스토어에 위임한다.
 * DB(파이프라인 상태) 연동 시 스토어 호출부를 수집 상태 조회로 교체.
 */
@Service
public class SourceService {

	private final SourceMockStore store;

	public SourceService(SourceMockStore store) {
		this.store = store;
	}

	public SourceReport report() {
		return store.report();
	}
}
