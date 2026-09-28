package com.edge.app.community.post.event;

/** 답글 생성 이벤트. 알림 적재는 notification 도메인 소유 */
public record ReplyCreated(long postId, long postAuthorId, long replyAuthorId, String body) {
}
