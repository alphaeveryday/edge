package com.edge.app.controller;

import com.edge.app.dto.MyVoteResponse;
import com.edge.app.dto.VoteCountResponse;
import com.edge.app.dto.VoteRequest;
import com.edge.app.service.VoteService;
import com.edge.common.apipayload.ApiResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequiredArgsConstructor
public class VoteController {

	private final VoteService voteService;

	@PutMapping("/api/forecasts/{id}/vote")
	public ApiResponse<VoteCountResponse> vote(
			@PathVariable long id,
			@RequestHeader("X-User-Id") long userId,
			@RequestBody VoteRequest request) {
		return ApiResponse.onSuccess(voteService.vote(id, userId, request.choice()));
	}

	@GetMapping("/api/me/votes")
	public ApiResponse<List<MyVoteResponse>> myVotes(@RequestHeader("X-User-Id") long userId) {
		return ApiResponse.onSuccess(voteService.myVotes(userId));
	}
}
