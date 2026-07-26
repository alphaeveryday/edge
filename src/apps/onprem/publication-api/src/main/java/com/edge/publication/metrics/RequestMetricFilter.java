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
import tools.jackson.databind.JsonNode;
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
	/** 스키마 varchar 상한(serving_request_metric.method/route/error_code)과 동일. */
	private static final int METHOD_MAX_LENGTH = 10;
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
		// (한계: 비동기 핸들러(Callable 등)는 현 표면에 없다 — 도입 시 이 필터의 async
		// dispatch 처리를 함께 재설계해야 한다.)
		ContentCachingResponseWrapper wrapper = new ContentCachingResponseWrapper(response);
		boolean recorded = false;
		try {
			chain.doFilter(request, wrapper);
			record(request, wrapper.getStatus(), wrapper);
			recorded = true;
			// 본문 복사는 성공 경로에서만 — 예외 경로에서 부분 본문을 커밋하면 컨테이너
			// ERROR 처리기가 정상 500 응답으로 교체할 수 없게 된다.
			wrapper.copyBodyToResponse();
		} catch (Exception e) {
			// advice 밖으로 샌 미처리 예외 — 컨테이너 ERROR dispatch 로 500 이 되지만
			// OncePerRequestFilter 는 그 dispatch 를 다시 타지 않으므로 여기서 실제
			// 결말(500)로 기록하고, 응답은 복사 없이 ERROR 처리기에 맡긴다. 이미 기록된
			// 요청(본문 복사 실패 등 기록 이후의 예외)은 이중 적재하지 않는다.
			if (!recorded) {
				record(request, 500, null);
			}
			throw e;
		}
	}

	private void record(HttpServletRequest request, int status,
			ContentCachingResponseWrapper bodySource) {
		try {
			String errorCode = status >= 400 && bodySource != null ? errorCode(bodySource) : null;
			metrics.save(new ServingRequestMetric(truncate(request.getMethod(), METHOD_MAX_LENGTH),
					route(request), (short) status, errorCode));
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
		return truncate(route, ROUTE_MAX_LENGTH);
	}

	private String errorCode(ContentCachingResponseWrapper response) {
		byte[] body = response.getContentAsByteArray();
		if (body.length == 0) {
			return null;
		}
		try {
			JsonNode codeNode = objectMapper.readTree(body).path("code");
			// 어휘는 문자열 도메인 코드(SERV*·COMMON*)뿐 — 숫자/불리언의 문자열 강제 변환·
			// 빈/과대 값은 NULL(미상)로 수렴한다(ck_serving_request_metric_error_code 규율).
			if (!codeNode.isString()) {
				return null;
			}
			String code = codeNode.asString();
			return code.isBlank() || code.length() > ERROR_CODE_MAX_LENGTH ? null : code;
		} catch (Exception e) {
			return null;
		}
	}

	private static String truncate(String value, int maxLength) {
		return value.length() > maxLength ? value.substring(0, maxLength) : value;
	}
}
