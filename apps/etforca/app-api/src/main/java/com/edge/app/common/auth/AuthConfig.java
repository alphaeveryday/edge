package com.edge.app.common.auth;

import org.springdoc.core.utils.SpringDocUtils;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import java.util.List;

@Configuration
@EnableConfigurationProperties(JwtProperties.class)
public class AuthConfig implements WebMvcConfigurer {
    static {
        // 리졸버 인자의 springdoc 문서 제외
        SpringDocUtils.getConfig().addRequestWrapperToIgnore(MemberPrincipal.class, AppPrincipal.class);
    }

    @Override
    public void addArgumentResolvers(List<HandlerMethodArgumentResolver> resolvers) {
        resolvers.add(new PrincipalArgumentResolver());
    }
}
