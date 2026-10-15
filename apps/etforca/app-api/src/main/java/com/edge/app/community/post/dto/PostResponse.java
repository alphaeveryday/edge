package com.edge.app.community.post.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.Instant;

public record PostResponse(String id, Etf etf, Author author, Instant time, String title, String body, String quoteTag,
        int like, int comment, boolean liked, int views, boolean mine, Boolean blocked) {
    // 상세 조회 전용 표시
    // 목록에서의 키 생략
    public PostResponse asBlocked() {
        return new PostResponse(id, etf, author, time, title, body, quoteTag, like, comment, liked, views,
                mine, true);
    }

    // 1.1.0 이전 앱이 읽는 댓글 수 필드
    @JsonProperty("reply")
    public int legacyReply() {
        return comment;
    }

    // 예약어 short 회피용 컴포넌트명
    public record Etf(String code, String theme, @JsonProperty("short") String shortName) {
    }

    public record Author(String name, String handle) {
    }
}
