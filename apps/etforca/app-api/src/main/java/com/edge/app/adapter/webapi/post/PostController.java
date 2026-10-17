package com.edge.app.adapter.webapi.post;

import com.edge.app.adapter.security.MemberPrincipal;
import com.edge.app.application.member.provided.AppPrincipal;
import com.edge.app.application.post.PostService;
import com.edge.app.application.post.provided.CommentCreateRequest;
import com.edge.app.application.post.provided.CommentResponse;
import com.edge.app.application.post.provided.PostCreateRequest;
import com.edge.app.application.post.provided.PostResponse;
import com.edge.app.domain.post.PostScope;
import com.edge.app.support.cursor.Cursor;
import com.edge.app.support.cursor.PageResponse;
import com.edge.common.apipayload.ApiResponse;
import io.swagger.v3.oas.annotations.Hidden;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import lombok.RequiredArgsConstructor;
import org.jspecify.annotations.Nullable;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/posts")
@RequiredArgsConstructor
public class PostController {
    private final PostService postService;

    @GetMapping
    public ApiResponse<PageResponse<PostResponse>> communityFeed(AppPrincipal principal,
            @RequestParam(defaultValue = "all") PostScope scope, @RequestParam(required = false) String code,
            @RequestParam(required = false) String cursor, @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size) {
        return ApiResponse.onSuccess(postService.feed(principal, scope, code, cursor == null ? null : Cursor.decode(cursor), size));
    }

    @PostMapping
    public ApiResponse<PostResponse> communityCreate(MemberPrincipal principal, @RequestBody @Valid PostCreateRequest request) {
        return ApiResponse.onSuccess(postService.create(principal.memberId(), request));
    }

    // 요청자가 있을 때 liked·mine 을 채우는 공개 조회
    @GetMapping("/{id}")
    public ApiResponse<PostResponse> communityGet(@PathVariable String id, @Nullable AppPrincipal principal) {
        return ApiResponse.onSuccess(postService.get(id, principal));
    }

    @DeleteMapping("/{id}")
    public ApiResponse<Void> communityRemove(MemberPrincipal principal, @PathVariable String id) {
        postService.remove(principal.memberId(), id);
        return ApiResponse.onSuccess(null);
    }

    // 요청자가 있을 때 차단한 작성자를 빼는 공개 조회
    @GetMapping("/{id}/comments")
    public ApiResponse<PageResponse<CommentResponse>> communityComments(@PathVariable String id, @Nullable AppPrincipal principal,
            @RequestParam(required = false) String cursor, @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size) {
        return ApiResponse.onSuccess(postService.comments(id, principal, cursor == null ? null : Cursor.decode(cursor), size));
    }

    @PostMapping("/{id}/comments")
    public ApiResponse<CommentResponse> communityComment(MemberPrincipal principal, @PathVariable String id,
            @RequestBody @Valid CommentCreateRequest request) {
        return ApiResponse.onSuccess(postService.comment(principal.memberId(), id, request));
    }

    @DeleteMapping("/{id}/comments/{commentId}")
    public ApiResponse<Void> communityRemoveComment(MemberPrincipal principal, @PathVariable String id, @PathVariable String commentId) {
        postService.removeComment(principal.memberId(), id, commentId);
        return ApiResponse.onSuccess(null);
    }

    // 1.1.0 이전 앱이 부르는 옛 경로, 계약 문서 제외
    @Hidden
    @GetMapping("/{id}/replies")
    public ApiResponse<PageResponse<CommentResponse>> legacyReplies(@PathVariable String id, @Nullable AppPrincipal principal,
            @RequestParam(required = false) String cursor, @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size) {
        return communityComments(id, principal, cursor, size);
    }

    @Hidden
    @PostMapping("/{id}/replies")
    public ApiResponse<CommentResponse> legacyReply(MemberPrincipal principal, @PathVariable String id,
            @RequestBody @Valid CommentCreateRequest request) {
        return communityComment(principal, id, request);
    }

    @Hidden
    @DeleteMapping("/{id}/replies/{replyId}")
    public ApiResponse<Void> legacyRemoveReply(MemberPrincipal principal, @PathVariable String id, @PathVariable String replyId) {
        return communityRemoveComment(principal, id, replyId);
    }

    @PutMapping("/{id}/like")
    public ApiResponse<PostResponse> communityLike(MemberPrincipal principal, @PathVariable String id) {
        return ApiResponse.onSuccess(postService.like(principal.memberId(), id));
    }

    @DeleteMapping("/{id}/like")
    public ApiResponse<PostResponse> communityUnlike(MemberPrincipal principal, @PathVariable String id) {
        return ApiResponse.onSuccess(postService.unlike(principal.memberId(), id));
    }
}
