package com.edge.tenantconsole.service;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.repository.MemberRepository;
import org.springframework.stereotype.Service;

/**
 * 콘솔 세션 표면(ALPHA-500) — 표시 이름은 member 원장이 SSOT 다(mock 스토어 제거).
 * 테넌트 컨텍스트는 설정(console.tenant.*)이 소스라 이 서비스는 관여하지 않는다.
 */
@Service
public class ConsoleSessionService {

	private final MemberRepository memberRepository;

	public ConsoleSessionService(MemberRepository memberRepository) {
		this.memberRepository = memberRepository;
	}

	/** 표시 이름 상한 — 전 역할 셀프서비스 표면이라 무제한 TEXT 영구 저장을 막는다. */
	static final int MAX_NAME_LENGTH = 100;

	/**
	 * 표시 이름 변경 — blank·100자 초과는 400, trim 후 세션 주체 본인의 원장 행을
	 * UPDATE 한다. 0행이면 404(기록 없는 성공 방지 백스톱). 갱신된 이름을 반환한다.
	 */
	public String updateDisplayName(long memberId, String name) {
		if (name == null || name.isBlank()) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		String trimmed = name.trim();
		if (trimmed.length() > MAX_NAME_LENGTH) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		if (memberRepository.updateName(memberId, trimmed) == 0) {
			throw new GeneralException(ConsoleErrorStatus.MEMBER_NOT_FOUND);
		}
		return trimmed;
	}
}
