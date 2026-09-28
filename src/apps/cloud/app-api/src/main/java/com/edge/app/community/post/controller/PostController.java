package com.edge.app.community.post.controller;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.common.auth.MemberPrincipal;
import com.edge.app.common.cursor.Cursor;
import com.edge.app.common.cursor.PageResponse;
import com.edge.app.community.post.dto.PostCreateRequest;
import com.edge.app.community.post.dto.PostResponse;
import com.edge.app.community.post.dto.ReplyCreateRequest;
import com.edge.app.community.post.dto.ReplyResponse;
import com.edge.app.community.post.service.PostService;
import com.edge.common.apipayload.ApiResponse;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import lombok.RequiredArgsConstructor;
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
            @RequestParam(defaultValue = "all") String scope, @RequestParam(required = false) String code,
            @RequestParam(required = false) String cursor, @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size) {
        return ApiResponse.onSuccess(postService.feed(principal, scope, code, cursor == null ? null : Cursor.decode(cursor), size));
    }

    @PostMapping
    public ApiResponse<PostResponse> communityCreate(MemberPrincipal principal, @RequestBody @Valid PostCreateRequest request) {
        return ApiResponse.onSuccess(postService.create(principal.memberId(), request));
    }

    @GetMapping("/{id}")
    public ApiResponse<PostResponse> communityGet(@PathVariable String id) {
        return ApiResponse.onSuccess(postService.get(id));
    }

    @DeleteMapping("/{id}")
    public ApiResponse<Void> communityRemove(MemberPrincipal principal, @PathVariable String id) {
        postService.remove(principal.memberId(), id);
        return ApiResponse.onSuccess(null);
    }

    @GetMapping("/{id}/replies")
    public ApiResponse<PageResponse<ReplyResponse>> communityReplies(@PathVariable String id,
            @RequestParam(required = false) String cursor, @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size) {
        return ApiResponse.onSuccess(postService.replies(id, cursor == null ? null : Cursor.decode(cursor), size));
    }

    @PostMapping("/{id}/replies")
    public ApiResponse<ReplyResponse> communityReply(MemberPrincipal principal, @PathVariable String id,
            @RequestBody @Valid ReplyCreateRequest request) {
        return ApiResponse.onSuccess(postService.reply(principal.memberId(), id, request));
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
