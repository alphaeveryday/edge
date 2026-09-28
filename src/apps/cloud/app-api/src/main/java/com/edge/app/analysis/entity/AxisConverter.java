package com.edge.app.analysis.entity;

import org.springframework.core.convert.converter.Converter;
import org.springframework.stereotype.Component;

/** 경로 변수 axis 소문자 변환. 모르는 값은 COMMON400 */
@Component
public class AxisConverter implements Converter<String, Axis> {
    @Override
    public Axis convert(String source) {
        return Axis.of(source);
    }
}
