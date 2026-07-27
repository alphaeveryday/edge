package com.edge.superadmin.service;

import com.edge.common.exception.GeneralException;
import com.edge.superadmin.dto.SourceReportResponse;
import com.edge.superadmin.error.AdminErrorStatus;
import com.edge.superadmin.repository.PipelineStatusRepository;
import org.springframework.stereotype.Service;

/**
 * sources 화면 — 운영 원장(`ops_*`)의 런 하나를 읽어 그대로 낸다(ALPHA-514, 드릴다운 574).
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

	/**
	 * @param runKey 지목할 런의 슬롯 키. <b>파라미터가 아예 없을 때만</b>(null) 최신 런이다 —
	 *               기존 호출과 동일한 동작. 공백 문자열은 "안 준 것"이 아니라 <b>그런 키의 런이
	 *               없는 것</b>이라 아래 조회를 그대로 타 404 가 된다. 공백을 부재로 관대하게
	 *               읽으면, 키를 지목했다고 믿는 화면이 최신 런을 받아 <b>옛 런으로 오독</b>한다.
	 * @throws GeneralException 지목한 런이 원장에 없으면 404. <b>빈 리포트로 대신하지 않는다</b> —
	 *                          오타 친 런 키가 "원장이 비어 있다"로 보이면 운영자는 없는 사실을
	 *                          있는 것처럼 읽는다(빈 원장은 그 자체로 정상 상태다).
	 */
	public SourceReportResponse report(String runKey) {
		if (runKey == null) {
			return pipelineStatus.latestRun()
					.map(SourceReportResponse::from)
					.orElseGet(SourceReportResponse::empty);
		}
		return pipelineStatus.runByKey(runKey)
				.map(SourceReportResponse::from)
				.orElseThrow(() -> new GeneralException(AdminErrorStatus.RUN_NOT_FOUND));
	}
}
