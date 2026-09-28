package com.edge.app.issue.controller;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.common.cursor.Cursor;
import com.edge.app.common.cursor.PageResponse;
import com.edge.app.issue.dto.IssueDetailResponse;
import com.edge.app.issue.dto.IssueRowResponse;
import com.edge.app.issue.service.IssueService;
import com.edge.common.apipayload.ApiResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/issues")
@RequiredArgsConstructor
public class IssueController {
    private final IssueService issueService;

    @GetMapping
    public ApiResponse<PageResponse<IssueRowResponse>> issueList(AppPrincipal principal, @RequestParam String tab,
            @RequestParam(required = false) String cursor, @RequestParam(defaultValue = "20") int size) {
        return ApiResponse.onSuccess(issueService.list(principal, tab, cursor == null ? null : Cursor.decode(cursor), size));
    }

    @GetMapping("/{id}")
    public ApiResponse<IssueDetailResponse> issueGet(@PathVariable String id) {
        return ApiResponse.onSuccess(issueService.get(id));
    }
}
