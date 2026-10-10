package com.edge.app.community.post.event;

/** 그 답글로 생긴 알림의 정리를 notification 도메인에 맡기는 답글 삭제 이벤트 */
public record ReplyDeleted(long replyId) {
}
