package com.edge.app.community.post.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.Instant;

public record PostResponse(String id, Etf etf, Author author, Instant time, String title, String body, String quoteTag,
        RepostOf repostOf, int like, int reply, int repost, boolean liked, int views, boolean mine) {
    // 예약어 회피. 컴포넌트명 shortName, 와이어 키 short
    public record Etf(String code, String theme, @JsonProperty("short") String shortName) {
    }

    public record Author(String name, String handle) {
    }

    public record RepostOf(String name, String handle, Instant time, String body) {
    }
}
