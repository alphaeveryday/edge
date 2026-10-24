package com.edge.tenantconsole.service;

import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.entity.ConsoleActionLogEntity;
import com.edge.tenantconsole.repository.ConsoleActionLogRepository;
import org.springframework.stereotype.Service;
import tools.jackson.databind.ObjectMapper;

import java.util.Map;

/**
 * 콘솔 조작 감사 기록 — 상태 변경(사용자 등록·비활성화, 역할 부여 등)이 "누가·무엇을"
 * 남기도록 console_action_log 에 append 한다(ALPHA-119 도입, ALPHA-499 등과 공유).
 * 조회는 두지 않는다(감사 열람 화면 없음 — console-ia 기준). actor 는 세션에서,
 * detail 은 액션별 부가정보(JSONB)다.
 */
@Service
public class ConsoleActionLogService {

	private final ConsoleActionLogRepository repository;
	private final ObjectMapper objectMapper;

	public ConsoleActionLogService(ConsoleActionLogRepository repository, ObjectMapper objectMapper) {
		this.repository = repository;
		this.objectMapper = objectMapper;
	}

	/** 감사 레코드 1건 append. detail 이 null 이면 JSONB 도 null 로 남긴다. */
	public void record(SessionMember actor, String action, String targetType, String targetId,
			Map<String, Object> detail, String clientIp) {
		String detailJson = detail == null ? null : objectMapper.writeValueAsString(detail);
		repository.save(new ConsoleActionLogEntity(
				actor.memberId(), action, targetType, targetId, detailJson, clientIp));
	}
}
