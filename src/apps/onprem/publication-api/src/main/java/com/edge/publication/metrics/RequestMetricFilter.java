package com.edge.publication.metrics;

import com.edge.publication.entity.ServingRequestMetric;
import com.edge.publication.repository.ServingRequestMetricRepository;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.servlet.HandlerMapping;
import org.springframework.web.util.ContentCachingResponseWrapper;
import org.springframework.web.util.pattern.PathPattern;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;

/**
 * 요청 메트릭 필터(ALPHA-501) — /api/** 요청의 수·상태·에러 코드를
 * serving_request_metric 에 기록한다(Dashboard ALPHA-128 데이터 소스).
 *
 * route 는 원시 URI 가 아니라 MVC 매핑 패턴이다 — 카디널리티 통제·경로 파라미터
 * (티커 등) 유입 방지. 에러 코드는 실패(4xx·5xx) 응답 본문(공통 봉투)의 code 만
 * 파싱한다 — 비JSON·형상 밖 응답은 NULL(코드 미상)이 정직한 값(스키마 CHECK 와 동일
 * 규율). 기록 실패는 로그로 드러내되 서빙 응답을 깨뜨리지 않는다 — 감사(exposure_log,
 * 같은 트랜잭션 fail-loud)와 달리 관측이 서빙을 죽이면 주객전도라는 의도적 선택.
 */
@Component
public class RequestMetricFilter extends OncePerRequestFilter {

	private static final Logger log = LoggerFactory.getLogger(RequestMetricFilter.class);
	/** 스키마 varchar 상한(serving_request_metric.route/error_code)과 동일. */
	private static final int ROUTE_MAX_LENGTH = 150;
	private static final int ERROR_CODE_MAX_LENGTH = 20;

	private final ServingRequestMetricRepository metrics;
	private final ObjectMapper objectMapper = new ObjectMapper();

	public RequestMetricFilter(ServingRequestMetricRepository metrics) {
		this.metrics = metrics;
	}

	@Override
	protected boolean shouldNotFilter(HttpServletRequest request) {
		// 콘솔·관측 경로(actuator 등)는 서빙 메트릭 대상이 아니다.
		return !request.getRequestURI().startsWith("/api/");
	}

	@Override
	protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
			FilterChain chain) throws ServletException, IOException {
		// 에러 코드는 커밋된 응답 본문에만 있어 캐싱 래퍼로 감싼다 — 본문 복사는 finally
		// 에서 항상 수행해 기록 여부와 무관하게 클라이언트 응답을 지킨다.
		ContentCachingResponseWrapper wrapper = new ContentCachingResponseWrapper(response);
		try {
			chain.doFilter(request, wrapper);
		} finally {
			record(request, wrapper);
			wrapper.copyBodyToResponse();
		}
	}

	private void record(HttpServletRequest request, ContentCachingResponseWrapper response) {
		try {
			int status = response.getStatus();
			metrics.save(new ServingRequestMetric(request.getMethod(), route(request),
					(short) status, status >= 400 ? errorCode(response) : null));
		} catch (Exception e) {
			log.error("요청 메트릭 기록 실패 — 서빙 응답은 유지한다 (method={} uri={})",
					request.getMethod(), request.getRequestURI(), e);
		}
	}

	private static String route(HttpServletRequest request) {
		Object pattern = request.getAttribute(HandlerMapping.BEST_MATCHING_PATTERN_ATTRIBUTE);
		String route = switch (pattern) {
			case PathPattern p -> p.getPatternString();
			case null -> "UNMATCHED";   // 매핑 없는 요청(프레임워크 404 등)
			default -> pattern.toString();
		};
		return route.length() > ROUTE_MAX_LENGTH ? route.substring(0, ROUTE_MAX_LENGTH) : route;
	}

	private String errorCode(ContentCachingResponseWrapper response) {
		byte[] body = response.getContentAsByteArray();
		if (body.length == 0) {
			return null;
		}
		try {
			String code = objectMapper.readTree(body).path("code").asString(null);
			// 빈/과대 코드는 어휘 밖 — NULL(미상)로 수렴한다(ck_serving_request_metric_error_code).
			return code == null || code.isBlank() || code.length() > ERROR_CODE_MAX_LENGTH
					? null : code;
		} catch (Exception e) {
			return null;
		}
	}
}
