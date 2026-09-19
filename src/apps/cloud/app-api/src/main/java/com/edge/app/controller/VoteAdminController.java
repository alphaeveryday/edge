package com.edge.app.controller;

import com.edge.app.service.VoteReconciler;
import com.edge.common.apipayload.ApiResponse;
import com.edge.common.apipayload.code.status.ErrorStatus;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

@RestController
@ConditionalOnProperty(name = "vote.mode", havingValue = "db-first", matchIfMissing = true)
@RequiredArgsConstructor
public class VoteAdminController {
    private final VoteReconciler voteReconciler;

    @Value("${vote.admin-token:}")
    private String adminToken;

    @PostMapping("/api/v1/admin/votes/reconcile")
    public ApiResponse<Boolean> reconcile(@RequestHeader(value = "X-Admin-Token", defaultValue = "") String token) {
        if (adminToken.isBlank() || !adminToken.equals(token)) {
            throw new GeneralException(ErrorStatus._FORBIDDEN);
        }
        return ApiResponse.onSuccess(voteReconciler.request());
    }
}
