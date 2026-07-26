package com.edge.superadmin.service;

import com.edge.superadmin.dto.SourceReportResponse;
import com.edge.superadmin.repository.PipelineStatusRepository;
import org.springframework.stereotype.Service;

/**
 * sources 화면 — 운영 원장(`ops_*`)의 최신 런을 읽어 그대로 낸다(ALPHA-514, 구 mock 표면 대체).
 *
 * <p>여기서 상태를 요약·판정하지 않는다. 원장이 이미 4축(plan_status·task_outcome·
 * execution_status·data_status)으로 판정을 끝냈고, 그걸 콘솔이 다시 뭉개면 <b>다섯 번째 어휘</b>가
 * 생긴다(ALPHA-181 이 새 상태 테이블을 안 만든 것과 같은 이유). 표시 라벨은 UI 소관이다.
 */
@Service
public class SourceService {

	private final PipelineStatusRepository pipelineStatus;

	public SourceService(PipelineStatusRepository pipelineStatus) {
		this.pipelineStatus = pipelineStatus;
	}

	public SourceReportResponse report() {
		return pipelineStatus.latestRun()
				.map(SourceReportResponse::from)
				.orElseGet(SourceReportResponse::empty);
	}
}
