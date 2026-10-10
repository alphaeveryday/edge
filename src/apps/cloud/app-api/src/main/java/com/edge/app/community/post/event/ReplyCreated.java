package com.edge.app.community.post.event;

/** 알림 적재를 notification 도메인에 맡기는 답글 생성 이벤트 */
public record ReplyCreated(long postId, long replyId, long postAuthorId, long replyAuthorId, String body) {
}
