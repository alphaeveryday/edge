package com.edge.app.community.vote.dto;

import com.edge.app.community.vote.entity.VoteChoice;
import com.fasterxml.jackson.annotation.JsonInclude;

// 미투표의 choice null 명시
public record MyVoteResponse(@JsonInclude(JsonInclude.Include.ALWAYS) VoteChoice choice) {
}
