package com.edge.tenantconsole.auth;

import com.edge.tenantconsole.entity.MemberEntity;
import com.edge.tenantconsole.repository.MemberRepository;
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
 * 인가는 매 요청 원장(member)에서 계정 상태를 재확인한다(ALPHA-119) — 세션 캐시가
 * 아니라 현재 is_active·role 로 판정하므로, 로그인 이후의 비활성화·역할 변경이 세션
 * 만료를 기다리지 않고 즉시 반영된다(비활성·삭제 계정은 세션 무효화 후 재로그인 요구).
 */
@Component
public class ConsoleAuthFilter extends OncePerRequestFilter {

	private static final Logger log = LoggerFactory.getLogger(ConsoleAuthFilter.class);

	private final MemberRepository memberRepository;

	public ConsoleAuthFilter(MemberRepository memberRepository) {
		this.memberRepository = memberRepository;
	}

	private static final Set<String> ANY_ROLE = Set.of(
			"TENANT_ADMIN", "COMPLIANCE_REVIEWER", "OPERATOR", "READ_ONLY");
	private static final Set<String> COMPLIANCE_REVIEWER_ONLY = Set.of("COMPLIANCE_REVIEWER");
	private static final Set<String> TENANT_ADMIN_ONLY = Set.of("TENANT_ADMIN");

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
			new Rule("POST", Pattern.compile("/api/v1/review/items/[^/]+/approve-edited"), COMPLIANCE_REVIEWER_ONLY),
			new Rule("POST", Pattern.compile("/api/v1/review/items/[^/]+/reject"), COMPLIANCE_REVIEWER_ONLY),
			new Rule("POST", Pattern.compile("/api/v1/review/items/[^/]+/block"), COMPLIANCE_REVIEWER_ONLY),
			new Rule("POST", Pattern.compile("/api/v1/auth/logout"), ANY_ROLE),
			new Rule("GET", Pattern.compile("/api/v1/auth/session"), ANY_ROLE),
			// ── 콘솔 mock 표면(ALPHA-513) — 인증만 강제(전 역할). UI 에 로그인·역할
			// 화면이 없는 mock 데이터 단계의 임시 완화로, 도메인별 DB 연동 시
			// permission-matrix.md 의 역할 세분화(검수·정책=CR, 사용자·시장=TA)를 적용한다.
			new Rule("GET", Pattern.compile("/api/v1/dashboard/traffic"), ANY_ROLE),
			new Rule("GET", Pattern.compile("/api/v1/explanations"), ANY_ROLE),
			new Rule("GET", Pattern.compile("/api/v1/explanations/feed-status"), ANY_ROLE),
			new Rule("PATCH", Pattern.compile("/api/v1/explanations/[^/]+/final"), ANY_ROLE),
			new Rule("POST", Pattern.compile("/api/v1/explanations/[^/]+/stop"), ANY_ROLE),
			new Rule("POST", Pattern.compile("/api/v1/explanations/[^/]+/move-to-review"), ANY_ROLE),
			new Rule("POST", Pattern.compile("/api/v1/explanations/[^/]+/approve"), ANY_ROLE),
			new Rule("POST", Pattern.compile("/api/v1/explanations/[^/]+/reject"), ANY_ROLE),
			new Rule("PATCH", Pattern.compile("/api/v1/explanations/[^/]+/draft"), ANY_ROLE),
			// 정책 조회는 전 역할, 변경(=새 버전 발행)은 CR 전용 — screening 도메인 DB
			// 전환(ALPHA-438)으로 mock 한시 예외가 해제됐다(permission-matrix "정책 변경" 행).
			new Rule("GET", Pattern.compile("/api/v1/screening/words"), ANY_ROLE),
			new Rule("POST", Pattern.compile("/api/v1/screening/words"), COMPLIANCE_REVIEWER_ONLY),
			new Rule("POST", Pattern.compile("/api/v1/screening/words/[^/]+/toggle"), COMPLIANCE_REVIEWER_ONLY),
			new Rule("GET", Pattern.compile("/api/v1/screening/criteria"), ANY_ROLE),
			new Rule("PATCH", Pattern.compile("/api/v1/screening/criteria"), COMPLIANCE_REVIEWER_ONLY),
			new Rule("GET", Pattern.compile("/api/v1/screening/disclaimer"), ANY_ROLE),
			new Rule("PATCH", Pattern.compile("/api/v1/screening/disclaimer"), COMPLIANCE_REVIEWER_ONLY),
			new Rule("GET", Pattern.compile("/api/v1/screening/versions"), ANY_ROLE),
			new Rule("GET", Pattern.compile("/api/v1/scope/markets"), ANY_ROLE),
			new Rule("POST", Pattern.compile("/api/v1/scope/markets/[^/]+/toggle"), ANY_ROLE),
			new Rule("GET", Pattern.compile("/api/v1/scope/stocks"), ANY_ROLE),
			new Rule("POST", Pattern.compile("/api/v1/scope/stocks/[^/]+/toggle"), ANY_ROLE),
			// 사용자 관리(ALPHA-119) — mock 완화를 벗어나 permission-matrix.md 의 실
			// 권한(Users & Roles = TENANT_ADMIN 전용)을 적용한다. member 원장 실데이터.
			new Rule("GET", Pattern.compile("/api/v1/members"), TENANT_ADMIN_ONLY),
			new Rule("POST", Pattern.compile("/api/v1/members"), TENANT_ADMIN_ONLY),
			new Rule("POST", Pattern.compile("/api/v1/members/[^/]+/deactivate"), TENANT_ADMIN_ONLY),
			new Rule("PATCH", Pattern.compile("/api/v1/members/[^/]+/role"), TENANT_ADMIN_ONLY),
			new Rule("GET", Pattern.compile("/api/v1/session"), ANY_ROLE),
			new Rule("PATCH", Pattern.compile("/api/v1/session/profile"), ANY_ROLE));

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

		// 세션 캐시가 아니라 원장의 현재 상태로 인가한다(ALPHA-119) — 비활성화·역할 변경 즉시 반영.
		MemberEntity current = memberRepository.findByEmailAndActiveTrue(member.email()).orElse(null);
		if (current == null) {
			// 계정이 비활성화·삭제됨 → 세션을 무효화하고 재로그인을 요구한다(fail-closed).
			HttpSession session = request.getSession(false);
			if (session != null) {
				session.invalidate();
			}
			write(response, HttpServletResponse.SC_UNAUTHORIZED, "CNSL4011", "로그인이 필요합니다.");
			return;
		}

		// 세션 주체는 원장의 현재 정체성(id·name·role)으로 유지한다 — /session 등이 stale
		// 값을 반환하지 않고(name 은 프로필 변경 ALPHA-500 이 원장에 기록), DB 재시드로
		// 같은 이메일이 다른 id 로 재생성됐을 때 옛 id 로 다른 행을 갱신하는 사고를 막는다
		// (인가는 아래에서 이미 current.getRole() 로 최신).
		SessionMember fresh = new SessionMember(current.getMemberId(), member.email(),
				current.getName(), current.getRole());
		if (!fresh.equals(member)) {
			request.getSession().setAttribute(SessionMember.SESSION_KEY, fresh);
			member = fresh;
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
		if (!rule.roles().contains(current.getRole())) {
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
