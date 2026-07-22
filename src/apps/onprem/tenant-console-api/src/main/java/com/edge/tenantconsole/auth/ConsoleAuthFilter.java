package com.edge.tenantconsole.auth;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.servlet.http.HttpSession;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.util.UriUtils;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * 콘솔 인증·인가 필터 — fail-closed(ADR-0025: 미인증 접근은 전 화면 차단).
 * 권한의 SSOT 는 docs/console-ia/permission-matrix.md — 이 필터의 라우트 정책은
 * 그 매트릭스의 "API 매핑" 표와 1:1 이며, 엔드포인트 추가 시 표와 여기에 함께
 * 행을 더한다. 매핑 없는 /api/** 는 거부가 기본이다(fail-closed).
 *
 * 인가는 세션에 실린 role 을 신뢰한다 — 로그인 이후의 계정 비활성화·역할 회수는
 * 세션 만료·로그아웃 전까지 즉시 반영되지 않는다(데모 범위의 트레이드오프). 매
 * 요청 is_active·role 재검증은 사용자 관리(ALPHA-119)와 함께 도입한다.
 */
@Component
public class ConsoleAuthFilter extends OncePerRequestFilter {

	private static final Logger log = LoggerFactory.getLogger(ConsoleAuthFilter.class);

	private static final Set<String> ANY_ROLE = Set.of(
			"TENANT_ADMIN", "COMPLIANCE_REVIEWER", "OPERATOR", "READ_ONLY");
	private static final Set<String> COMPLIANCE_REVIEWER_ONLY = Set.of("COMPLIANCE_REVIEWER");

	private record Rule(String method, Pattern path, Set<String> roles) {
		boolean matches(String requestMethod, String requestPath) {
			return method.equals(requestMethod) && path.matcher(requestPath).matches();
		}
	}

	/** 로그인 자체는 세션이 없는 상태에서 호출된다 — 유일한 공개 API 표면. */
	private static final Rule PUBLIC_LOGIN =
			new Rule("POST", Pattern.compile("/api/v1/auth/login"), Set.of());

	// permission-matrix.md "API 매핑" 표의 코드 대응물.
	private static final List<Rule> RULES = List.of(
			new Rule("GET", Pattern.compile("/api/v1/review/items"), ANY_ROLE),
			new Rule("POST", Pattern.compile("/api/v1/review/items/[^/]+/approve"), COMPLIANCE_REVIEWER_ONLY),
			new Rule("POST", Pattern.compile("/api/v1/review/items/[^/]+/reject"), COMPLIANCE_REVIEWER_ONLY),
			new Rule("POST", Pattern.compile("/api/v1/auth/logout"), ANY_ROLE),
			new Rule("GET", Pattern.compile("/api/v1/auth/session"), ANY_ROLE));

	@Override
	protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
			FilterChain chain) throws ServletException, IOException {

		// 매칭은 MVC 의 PathPattern 과 같은 표현 — %-디코딩(예: %3B→;) 후 context-path
		// 제거, 세그먼트별 matrix parameter(;k=v) 제거 — 를 적용한다. raw URI 로 판정하면
		// `/api;x=y/...`·`/api%3Bx=y/...` 가 필터를 통째로 우회한다(fail-closed 위반).
		String decodedUri = UriUtils.decode(request.getRequestURI(), StandardCharsets.UTF_8);
		String contextPath = UriUtils.decode(request.getContextPath(), StandardCharsets.UTF_8);
		String path = normalize(decodedUri.substring(contextPath.length()));

		// 콘솔 API 밖(actuator 등)은 이 필터의 관심사가 아니다.
		if (!path.startsWith("/api/")) {
			chain.doFilter(request, response);
			return;
		}
		if (PUBLIC_LOGIN.matches(request.getMethod(), path)) {
			chain.doFilter(request, response);
			return;
		}

		SessionMember member = currentMember(request);
		if (member == null) {
			write(response, HttpServletResponse.SC_UNAUTHORIZED, "CNSL4011", "로그인이 필요합니다.");
			return;
		}

		Rule rule = RULES.stream()
				.filter(r -> r.matches(request.getMethod(), path)).findFirst().orElse(null);
		if (rule == null) {
			// 매핑 없는 표면은 배포 전 permission-matrix.md 매핑이 의무 — 누락은 fail-closed.
			log.error("권한 매핑 없는 콘솔 표면 거부: {} {} (permission-matrix.md 매핑 필요)",
					request.getMethod(), path);
			write(response, HttpServletResponse.SC_FORBIDDEN, "CNSL4030", "이 작업을 수행할 권한이 없습니다.");
			return;
		}
		if (!rule.roles().contains(member.role())) {
			write(response, HttpServletResponse.SC_FORBIDDEN, "CNSL4030", "이 작업을 수행할 권한이 없습니다.");
			return;
		}
		chain.doFilter(request, response);
	}

	// 세그먼트별 matrix parameter(첫 ';' 이후) 제거. MVC 매핑이 무시하는 부분을
	// 필터도 똑같이 무시해야 라우트 판정이 어긋나지 않는다.
	private String normalize(String path) {
		StringBuilder out = new StringBuilder(path.length());
		for (String segment : path.split("/", -1)) {
			int semicolon = segment.indexOf(';');
			out.append('/').append(semicolon >= 0 ? segment.substring(0, semicolon) : segment);
		}
		// split 이 선행 '/' 를 빈 세그먼트로 만들어 앞에 '/' 가 하나 더 붙는다 — 제거.
		return out.length() > 1 ? out.substring(1) : out.toString();
	}

	private SessionMember currentMember(HttpServletRequest request) {
		HttpSession session = request.getSession(false);
		if (session == null) {
			return null;
		}
		Object attribute = session.getAttribute(SessionMember.SESSION_KEY);
		return attribute instanceof SessionMember sessionMember ? sessionMember : null;
	}

	// 필터는 @RestControllerAdvice 를 타지 않으므로 공통 봉투(ApiResponse 형상)를 직접 쓴다.
	private void write(HttpServletResponse response, int status, String code, String message)
			throws IOException {
		response.setStatus(status);
		response.setContentType(MediaType.APPLICATION_JSON_VALUE);
		response.setCharacterEncoding("UTF-8");
		response.getWriter().write(
				"{\"isSuccess\":false,\"code\":\"" + code + "\",\"message\":\"" + message + "\"}");
	}
}
