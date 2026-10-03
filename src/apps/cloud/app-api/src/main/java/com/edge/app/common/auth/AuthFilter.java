package com.edge.app.common.auth;

import com.edge.common.apipayload.ApiResponse;
import com.edge.common.apipayload.code.status.ErrorStatus;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;

/**
  * DB 조회 없는 헤더 해석과 요청 속성 principal 설정
  * Bearer 우선 해석
  * Bearer 검증 실패 시 즉시 COMMON401 응답
  * Bearer 가 없을 때의 X-Device-Id 게스트 처리
  * 둘 다 없을 때의 익명 통과
 */
@Component
public class AuthFilter extends OncePerRequestFilter {
    static final String MEMBER_ATTR = AuthFilter.class.getName() + ".member";
    static final String DEVICE_ATTR = AuthFilter.class.getName() + ".device";
    private static final String BEARER = "Bearer ";
    private static final int DEVICE_KEY_MAX = 64;

    private final AccessTokens tokens;
    private final ObjectMapper objectMapper;

    public AuthFilter(AccessTokens tokens, ObjectMapper objectMapper) {
        this.tokens = tokens;
        this.objectMapper = objectMapper;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String authorization = request.getHeader("Authorization");
        if (authorization != null) {
            if (!authorization.startsWith(BEARER)) {
                reject(response, ErrorStatus._UNAUTHORIZED);
                return;
            }
            var memberId = tokens.verify(authorization.substring(BEARER.length()).trim());
            if (memberId.isEmpty()) {
                reject(response, ErrorStatus._UNAUTHORIZED);
                return;
            }
            request.setAttribute(MEMBER_ATTR, memberId.get());
        } else {
            String deviceKey = request.getHeader("X-Device-Id");
            if (deviceKey != null) {
                deviceKey = deviceKey.trim();
                if (deviceKey.isEmpty() || deviceKey.length() > DEVICE_KEY_MAX) {
                    reject(response, ErrorStatus._BAD_REQUEST);
                    return;
                }
                request.setAttribute(DEVICE_ATTR, deviceKey);
            }
        }
        chain.doFilter(request, response);
    }

    private void reject(HttpServletResponse response, ErrorStatus status) throws IOException {
        response.setStatus(status.getHttpStatus().value());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setCharacterEncoding("UTF-8");
        objectMapper.writeValue(response.getWriter(), ApiResponse.onFailure(status.getCode(), status.getMessage(), null));
    }
}
