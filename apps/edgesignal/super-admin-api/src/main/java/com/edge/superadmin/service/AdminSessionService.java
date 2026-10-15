package com.edge.superadmin.service;

import com.edge.common.exception.GeneralException;
import com.edge.superadmin.auth.SessionOperator;
import com.edge.superadmin.dto.OperatorProfileResponse;
import com.edge.superadmin.error.AdminErrorStatus;
import org.springframework.stereotype.Service;

import java.util.Locale;

/**
 * session 화면 표면 — 사이드바·헤더가 쓰는 운영자 컨텍스트를 인증 세션 주체
 * (SessionOperator)로부터 투영한다(ALPHA-608 실전환, 종전 mock 하드코딩 대체).
 * 운영자 계정 원장은 config 부트스트랩(ALPHA-515)이고, 로그인 시 그 계정이
 * SessionOperator 로 세션에 실린다 — 별도 저장소가 없어 이 표면은 세션 주체를
 * 읽기만 한다. 표시 이름 변경의 세션 반영은 컨트롤러가 세션 주체를 재바인딩한다.
 */
@Service
public class AdminSessionService {

	// 운영자는 단일 역할이라 role 은 상수다 — SessionOperator 에 role 필드가 없고
	// AdminAuthFilter 에 역할 인가가 없는 것과 같은 이유. UI 타입(OperatorSession.role)이
	// 'Owner' 리터럴로 고정돼 있어 이 값이 곧 계약이다. 역할 분화 시 세션 주체에 role 을
	// 싣고 여기서 읽는다.
	private static final String OPERATOR_ROLE = "Owner";

	/** 인증된 운영자를 콘솔 프로필로 투영한다(role 은 단일 역할 상수, initials 는 이름에서 파생). */
	public OperatorProfileResponse profileOf(SessionOperator operator) {
		return new OperatorProfileResponse(
				operator.name(), operator.email(), OPERATOR_ROLE, initials(operator.name()));
	}

	/** 표시 이름 변경 검증 — 빈 값 거부. 세션 반영(주체 재바인딩)은 컨트롤러 소관. */
	public String validatedDisplayName(String name) {
		if (name == null || name.isBlank()) {
			throw new GeneralException(AdminErrorStatus.INVALID_REQUEST);
		}
		return name.trim();
	}

	// 아바타 이니셜(표시용 파생값) — 두 토큰 이상이면 앞 두 토큰의 첫 글자, 단일
	// 토큰이면 앞 두 글자. UI 는 이 값을 그대로 렌더링만 한다(파생 규칙 비계약).
	private static String initials(String name) {
		String[] tokens = name.trim().split("\\s+");
		String picked = tokens.length >= 2
				? leadingChars(tokens[0], 1) + leadingChars(tokens[1], 1)
				: leadingChars(tokens[0], 2);
		return picked.toUpperCase(Locale.ROOT);
	}

	// 코드포인트 경계 기준 앞 n글자 — surrogate pair(이모지 등 보조 평면)를 쪼개
	// 깨진 문자를 만들지 않는다. 표시 이름은 PATCH 로 임의 유니코드가 들어올 수 있다.
	private static String leadingChars(String token, int n) {
		int count = Math.min(n, token.codePointCount(0, token.length()));
		return token.substring(0, token.offsetByCodePoints(0, count));
	}
}
