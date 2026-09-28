package com.edge.app.common.auth;

import com.edge.common.apipayload.code.status.ErrorStatus;
import com.edge.common.exception.GeneralException;
import org.springframework.core.MethodParameter;
import org.springframework.web.bind.support.WebDataBinderFactory;
import org.springframework.web.context.request.NativeWebRequest;
import org.springframework.web.context.request.RequestAttributes;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.method.support.ModelAndViewContainer;

/** 인자 타입이 요구 수준이다. MemberPrincipal 은 회원만, AppPrincipal 은 회원 또는 게스트. 없으면 COMMON401. */
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
            throw new GeneralException(ErrorStatus._UNAUTHORIZED);
        }
        return AppPrincipal.device(deviceKey);
    }
}
