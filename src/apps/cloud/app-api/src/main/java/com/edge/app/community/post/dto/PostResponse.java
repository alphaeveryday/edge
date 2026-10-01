package com.edge.app.community.post.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.Instant;

public record PostResponse(String id, Etf etf, Author author, Instant time, String title, String body, String quoteTag,
        int like, int reply, boolean liked, int views, boolean mine, Boolean blocked) {
    // 상세 조회 전용. 목록에서는 키 생략
    public PostResponse asBlocked() {
        return new PostResponse(id, etf, author, time, title, body, quoteTag, like, reply, liked, views,
                mine, true);
    }

    // 예약어 회피. 컴포넌트명 shortName, 와이어 키 short
    public record Etf(String code, String theme, @JsonProperty("short") String shortName) {
    }

    public record Author(String name, String handle) {
    }
}
