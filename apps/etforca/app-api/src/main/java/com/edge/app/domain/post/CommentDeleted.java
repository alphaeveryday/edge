package com.edge.app.domain.post;

/** 그 댓글로 생긴 알림의 정리를 notification 도메인에 맡기는 댓글 삭제 이벤트 */
public record CommentDeleted(long commentId) {
}
