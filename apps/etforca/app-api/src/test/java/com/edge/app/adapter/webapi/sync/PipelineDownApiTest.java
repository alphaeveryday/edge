package com.edge.app.adapter.webapi.sync;

import com.edge.app.support.stereotype.WebApiAdapterTest;
import org.junit.jupiter.api.Test;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.assertj.MockMvcTester;

import static org.assertj.core.api.Assertions.assertThat;

/** 파이프라인 RDS 불통 시에도 동기화 외 API 의 정상 응답 */
@WebApiAdapterTest
@TestPropertySource(properties = {
        "app.pipeline.url=jdbc:postgresql://localhost:1/edge", "app.pipeline.username=x", "app.pipeline.password=x",
        "app.sync.initial-delay=PT1H"})
record PipelineDownApiTest(MockMvcTester mvcTester) {
    @Test
    void appStartsAndServesWhenPipelineUnreachable() {
        assertThat(mvcTester.get().uri("/api/v1/themes").exchange()).hasStatusOk();
    }
}
