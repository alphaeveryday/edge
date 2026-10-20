package com.edge.app.application.post.provided;

import com.edge.app.application.member.provided.AppPrincipal;
import com.edge.app.domain.post.PostScope;
import com.edge.app.support.cursor.Cursor;
import com.edge.app.support.cursor.PageResponse;

/**
 * 글과 댓글 조회
 * - 상세 조회의 조회수 증가
 */
public interface PostFinder {
    PageResponse<PostView> feed(AppPrincipal principal, PostScope scope, String code, Cursor cursor, int size);

    PostView get(String id, AppPrincipal principal);

    PageResponse<CommentView> comments(String id, AppPrincipal principal, Cursor cursor, int size);
}
