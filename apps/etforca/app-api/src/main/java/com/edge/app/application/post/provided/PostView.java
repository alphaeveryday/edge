package com.edge.app.application.post.provided;

import java.time.Instant;

/**
 * 글 조회 모델
 * - 요청자 기준 liked·mine·blocked 와 탈퇴 회원 이름 가림 반영
 */
public record PostView(String id, Etf etf, Author author, Instant time, String title, String body, String quoteTag,
        int like, int comment, boolean liked, int views, boolean mine, boolean blocked) {
    public PostView asBlocked() {
        return new PostView(id, etf, author, time, title, body, quoteTag, like, comment, liked, views, mine, true);
    }

    public record Etf(String code, String theme, String shortName) {
    }

    public record Author(String name, String handle) {
    }
}
