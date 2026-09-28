package com.edge.app.community.post.entity;

import org.springframework.core.convert.converter.Converter;
import org.springframework.stereotype.Component;

/** 쿼리 파라미터 scope 소문자 변환. 모르는 값은 COMMON400 */
@Component
public class PostScopeConverter implements Converter<String, PostScope> {
    @Override
    public PostScope convert(String source) {
        return PostScope.of(source);
    }
}
