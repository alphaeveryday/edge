package com.edge.superadmin.auth;

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
import java.util.regex.Pattern;

/**
 * 운영자 인증 필터 — fail-closed(ALPHA-474: 공개 엣지의 앱 방어선, 미인증 접근은
 * 전 표면 차단). 표면의 SSOT 는 docs/console-ia/super-admin-console.md — 엔드포인트
 * 추가 시 IA 문서와 여기 RULES 에 함께 행을 더한다. 매핑 없는 /api/** 는 거부가
 * 기본이다(fail-closed). 운영자는 단일 역할이라 역할 인가는 없다 — 인증만 강제한다
 * (역할 분화 시 tenant-console ConsoleAuthFilter 처럼 Rule 에 역할 집합을 더한다).
 */
@Component
public class AdminAuthFilter extends OncePerRequestFilter {

	private static final Logger log = LoggerFactory.getLogger(AdminAuthFilter.class);

	private record Rule(String method, Pattern path) {
		boolean matches(String requestMethod, String requestPath) {
			return method.equals(requestMethod) && path.matcher(requestPath).matches();
		}
	}

	/** 로그인 자체는 세션이 없는 상태에서 호출된다 — 유일한 공개 API 표면. */
	private static final Rule PUBLIC_LOGIN =
			new Rule("POST", Pattern.compile("/api/v1/auth/login"));

	// super-admin-console.md 화면 표면의 코드 대응물(ALPHA-515) — 전부 인증 필수.
	private static final List<Rule> RULES = List.of(
			new Rule("POST", Pattern.compile("/api/v1/auth/logout")),
			new Rule("GET", Pattern.compile("/api/v1/auth/session")),
			new Rule("GET", Pattern.compile("/api/v1/tenants")),
			new Rule("POST", Pattern.compile("/api/v1/tenants")),
			new Rule("GET", Pattern.compile("/api/v1/sources/report")),
			new Rule("GET", Pattern.compile("/api/v1/sources/grid")),
			new Rule("GET", Pattern.compile("/api/v1/sources/overview")),
			new Rule("GET", Pattern.compile("/api/v1/sources/lineage/news")),
			new Rule("GET", Pattern.compile("/api/v1/sources/minute")),
			new Rule("GET", Pattern.compile("/api/v1/sources/impact/holdings")),
			new Rule("GET", Pattern.compile("/api/v1/analyses")),
			new Rule("POST", Pattern.compile("/api/v1/analyses/[^/]+/invalidate")),
			new Rule("GET", Pattern.compile("/api/v1/session")),
			new Rule("PATCH", Pattern.compile("/api/v1/session/profile")));

	@Override
	protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
			FilterChain chain) throws ServletException, IOException {

		// 매칭은 MVC 의 PathPattern 과 같은 표현 — %-디코딩(예: %3B→;) 후 context-path
		// 제거, 세그먼트별 matrix parameter(;k=v) 제거 — 를 적용한다. raw URI 로 판정하면
		// `/api;x=y/...`·`/api%3Bx=y/...` 가 필터를 통째로 우회한다(fail-closed 위반).
		String decodedUri = UriUtils.decode(request.getRequestURI(), StandardCharsets.UTF_8);
		String contextPath = UriUtils.decode(request.getContextPath(), StandardCharsets.UTF_8);
		String path = normalize(decodedUri.substring(contextPath.length()));

		// 콘솔 API 밖(actuator health 등)은 이 필터의 관심사가 아니다.
		if (!path.startsWith("/api/")) {
			chain.doFilter(request, response);
			return;
		}
		if (PUBLIC_LOGIN.matches(request.getMethod(), path)) {
			chain.doFilter(request, response);
			return;
		}

		if (currentOperator(request) == null) {
			write(response, HttpServletResponse.SC_UNAUTHORIZED, "ADMN4011", "로그인이 필요합니다.");
			return;
		}

		boolean mapped = RULES.stream()
				.anyMatch(r -> r.matches(request.getMethod(), path));
		if (!mapped) {
			// 매핑 없는 표면은 배포 전 RULES 등록이 의무 — 누락은 fail-closed.
			log.error("표면 매핑 없는 admin API 거부: {} {} (AdminAuthFilter RULES 등록 필요)",
					request.getMethod(), path);
			write(response, HttpServletResponse.SC_FORBIDDEN, "ADMN4030", "이 작업을 수행할 권한이 없습니다.");
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

	private SessionOperator currentOperator(HttpServletRequest request) {
		HttpSession session = request.getSession(false);
		if (session == null) {
			return null;
		}
		Object attribute = session.getAttribute(SessionOperator.SESSION_KEY);
		return attribute instanceof SessionOperator operator ? operator : null;
	}

	// 필터는 @RestControllerAdvice 를 타지 않으므로 공통 응답 포맷(ApiResponse 형상)을 직접 쓴다.
	private void write(HttpServletResponse response, int status, String code, String message)
			throws IOException {
		response.setStatus(status);
		response.setContentType(MediaType.APPLICATION_JSON_VALUE);
		response.setCharacterEncoding("UTF-8");
		response.getWriter().write(
				"{\"isSuccess\":false,\"code\":\"" + code + "\",\"message\":\"" + message + "\"}");
	}
}
