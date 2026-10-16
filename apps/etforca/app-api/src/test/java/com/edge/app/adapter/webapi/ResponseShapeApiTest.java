package com.edge.app.adapter.webapi;

import com.edge.app.support.stereotype.WebApiAdapterTest;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MvcTestResult;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 계약의 와이어 규약
 * 선택 필드의 키 생략과 nextCursor 의 null 키 유지
 * 1..100 밖 size 의 COMMON400 응답
 */
@WebApiAdapterTest
record ResponseShapeApiTest(MockMvcTester mvcTester) {
    @Test
    void lastPageKeepsNextCursorNull() {
        assertThat(get("/api/v1/notifications")).bodyText().contains("\"nextCursor\":null");
    }

    @Test
    void nullResultIsOmitted() {
        // POST 전용 경로에 GET 을 보낸 405 응답에서 result 키 자체가 없는 실패 봉투
        assertThat(get("/api/v1/notifications/read-all")).bodyText().doesNotContain("\"result\"");
    }

    @Test
    void sizeOutOfRangeIsBadRequest() {
        assertThat(get("/api/v1/notifications?size=101")).hasStatus(400);
        assertThat(get("/api/v1/notifications?size=0")).hasStatus(400);
        assertThat(get("/api/v1/notifications?size=101")).bodyText().contains("COMMON400");
        assertThat(get("/api/v1/notifications?size=100")).hasStatusOk();
    }

    private MvcTestResult get(String uri) {
        return mvcTester.get().uri(uri).header("X-Device-Id", "shape").exchange();
    }
}
