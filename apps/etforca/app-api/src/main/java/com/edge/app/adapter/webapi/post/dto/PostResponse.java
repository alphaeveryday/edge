package com.edge.app.adapter.webapi.post.dto;

import com.edge.app.application.post.provided.PostView;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.Instant;

public record PostResponse(String id, Etf etf, Author author, Instant time, String title, String body, String quoteTag,
        int like, int comment, boolean liked, int views, boolean mine, Boolean blocked) {
    // 상세 조회의 차단 글에만 blocked 표시
    // 그 외 키 생략
    public static PostResponse from(PostView v) {
        return new PostResponse(v.id(), new Etf(v.etf().code(), v.etf().theme(), v.etf().shortName()),
                Author.from(v.author()), v.time(), v.title(), v.body(), v.quoteTag(), v.like(), v.comment(), v.liked(),
                v.views(), v.mine(), v.blocked() ? Boolean.TRUE : null);
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
        public static Author from(PostView.Author a) {
            return a == null ? null : new Author(a.name(), a.handle());
        }
    }
}
