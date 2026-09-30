package com.edge.app.community.block.controller;

import com.edge.app.common.auth.MemberPrincipal;
import com.edge.app.community.block.service.BlockService;
import com.edge.common.apipayload.ApiResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/blocks")
@RequiredArgsConstructor
public class BlockController {
    private final BlockService blockService;

    @PutMapping("/{handle}")
    public ApiResponse<Void> communityBlock(MemberPrincipal principal, @PathVariable String handle) {
        blockService.block(principal.memberId(), handle);
        return ApiResponse.onSuccess(null);
    }
}
