package com.edge.app.application.post.provided;

/**
 * 글·댓글 작성과 삭제, 좋아요
 */
public interface PostModifier {
    PostView create(long memberId, PostCreateRequest request);

    void remove(long memberId, String id);

    CommentView comment(long memberId, String id, CommentCreateRequest request);

    void removeComment(long memberId, String id, String commentId);

    PostView like(long memberId, String id);

    PostView unlike(long memberId, String id);
}
