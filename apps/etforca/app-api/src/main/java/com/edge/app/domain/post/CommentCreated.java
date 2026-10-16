package com.edge.app.domain.post;

/** 알림 적재를 notification 도메인에 맡기는 댓글 생성 이벤트 */
public record CommentCreated(long postId, long commentId, long postAuthorId, long commentAuthorId, String body) {
}
