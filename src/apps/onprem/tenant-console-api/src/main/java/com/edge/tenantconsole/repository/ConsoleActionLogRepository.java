package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.ConsoleActionLogEntity;
import org.springframework.data.repository.Repository;

/**
 * console_action_log 접근 — writer = tenant-console-api(전유, 스키마 COMMENT).
 * append-only 원장이라 save(insert)만 노출한다(UPDATE·DELETE 미노출 — 감사 로그
 * 불변성을 구조적으로 강제).
 */
public interface ConsoleActionLogRepository extends Repository<ConsoleActionLogEntity, Long> {

	ConsoleActionLogEntity save(ConsoleActionLogEntity log);
}
