package com.edge.app.adapter.webapi.watch;

import com.edge.app.application.etf.provided.EtfSummaryResponse;
import com.edge.app.application.member.provided.AppPrincipal;
import com.edge.app.application.watch.provided.WatchFinder;
import com.edge.app.application.watch.provided.WatchGroupCreateRequest;
import com.edge.app.application.watch.provided.WatchGroupResponse;
import com.edge.app.application.watch.provided.WatchMembersRequest;
import com.edge.app.application.watch.provided.WatchMembershipRequest;
import com.edge.app.application.watch.provided.WatchModifier;
import com.edge.common.apipayload.ApiResponse;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/v1")
@RequiredArgsConstructor
public class WatchController {
    private final WatchFinder watchFinder;
    private final WatchModifier watchModifier;

    @GetMapping("/watch-groups")
    public ApiResponse<List<WatchGroupResponse>> watchGroups(AppPrincipal principal) {
        return ApiResponse.onSuccess(watchFinder.groups(principal));
    }

    @PostMapping("/watch-groups")
    public ApiResponse<WatchGroupResponse> watchCreateGroup(AppPrincipal principal,
            @RequestBody @Valid WatchGroupCreateRequest request) {
        return ApiResponse.onSuccess(watchModifier.createGroup(principal, request));
    }

    @DeleteMapping("/watch-groups/{group}")
    public ApiResponse<Void> watchDeleteGroup(AppPrincipal principal, @PathVariable String group) {
        watchModifier.deleteGroup(principal, group);
        return ApiResponse.onSuccess(null);
    }

    @GetMapping("/watch-groups/{group}/etfs")
    public ApiResponse<List<EtfSummaryResponse>> watchList(AppPrincipal principal, @PathVariable String group) {
        return ApiResponse.onSuccess(watchFinder.list(principal, group));
    }

    @PutMapping("/watch-groups/{group}/etfs")
    public ApiResponse<Void> watchSetMembers(AppPrincipal principal, @PathVariable String group,
            @RequestBody @Valid WatchMembersRequest request) {
        watchModifier.setMembers(principal, group, request);
        return ApiResponse.onSuccess(null);
    }

    @GetMapping("/etfs/{code}/watch-groups")
    public ApiResponse<List<String>> watchMembership(AppPrincipal principal, @PathVariable String code) {
        return ApiResponse.onSuccess(watchFinder.membership(principal, code));
    }

    @PutMapping("/etfs/{code}/watch-groups")
    public ApiResponse<Void> watchSetMembership(AppPrincipal principal, @PathVariable String code,
            @RequestBody @Valid WatchMembershipRequest request) {
        watchModifier.setMembership(principal, code, request);
        return ApiResponse.onSuccess(null);
    }
}
