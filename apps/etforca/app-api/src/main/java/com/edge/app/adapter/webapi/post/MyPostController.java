package com.edge.app.adapter.webapi.post;

import com.edge.app.adapter.security.MemberPrincipal;
import com.edge.app.adapter.webapi.post.dto.PostResponse;
import com.edge.app.application.post.provided.PostFinder;
import com.edge.app.application.post.provided.PostView;
import com.edge.app.support.cursor.Cursor;
import com.edge.app.support.cursor.PageResponse;
import com.edge.common.apipayload.ApiResponse;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/me/posts")
@RequiredArgsConstructor
public class MyPostController {
    private final PostFinder postFinder;

    @GetMapping
    public ApiResponse<PageResponse<PostResponse>> communityMyPosts(MemberPrincipal principal,
            @RequestParam(required = false) String cursor, @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size) {
        PageResponse<PostView> page = postFinder.authored(principal.memberId(), cursor == null ? null : Cursor.decode(cursor), size);
        return ApiResponse.onSuccess(new PageResponse<>(page.items().stream().map(PostResponse::from).toList(), page.nextCursor()));
    }
}
