package com.edge.app.community.post.event;

/** 글쓴이에게 알림을 만드는 것은 notification 도메인의 쓰기라 이벤트로 넘긴다. */
public record ReplyCreated(long postId, long postAuthorId, long replyAuthorId, String body) {
}
