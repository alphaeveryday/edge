package com.edge.app.common.auth;

import com.edge.common.apipayload.code.status.ErrorStatus;
import com.edge.common.exception.GeneralException;
import org.springframework.core.MethodParameter;
import org.springframework.web.bind.support.WebDataBinderFactory;
import org.springframework.web.context.request.NativeWebRequest;
import org.springframework.web.context.request.RequestAttributes;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.method.support.ModelAndViewContainer;

/**
  * 인자 타입으로 정하는 인증 요구 수준
  * MemberPrincipal 의 회원 한정
  * AppPrincipal 의 회원 또는 게스트 허용
  * 인증 주체 부재 시 COMMON401 응답
  * Nullable AppPrincipal 의 익명 통과와 null 주입
 */
public class PrincipalArgumentResolver implements HandlerMethodArgumentResolver {
    @Override
    public boolean supportsParameter(MethodParameter parameter) {
        Class<?> type = parameter.getParameterType();
        return type == MemberPrincipal.class || type == AppPrincipal.class;
    }

    @Override
    public Object resolveArgument(MethodParameter parameter, ModelAndViewContainer mavContainer,
            NativeWebRequest webRequest, WebDataBinderFactory binderFactory) {
        Long memberId = (Long) webRequest.getAttribute(AuthFilter.MEMBER_ATTR, RequestAttributes.SCOPE_REQUEST);
        if (parameter.getParameterType() == MemberPrincipal.class) {
            if (memberId == null) {
                throw new GeneralException(ErrorStatus._UNAUTHORIZED);
            }
            return new MemberPrincipal(memberId);
        }
        if (memberId != null) {
            return AppPrincipal.member(memberId);
        }
        String deviceKey = (String) webRequest.getAttribute(AuthFilter.DEVICE_ATTR, RequestAttributes.SCOPE_REQUEST);
        if (deviceKey == null) {
            if (parameter.isOptional()) {
                return null;
            }
            throw new GeneralException(ErrorStatus._UNAUTHORIZED);
        }
        return AppPrincipal.device(deviceKey);
    }
}
