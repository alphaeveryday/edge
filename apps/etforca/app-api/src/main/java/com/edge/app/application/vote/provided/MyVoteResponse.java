package com.edge.app.application.vote.provided;

import com.edge.app.domain.vote.VoteChoice;
import com.fasterxml.jackson.annotation.JsonInclude;

// 미투표의 choice null 명시
public record MyVoteResponse(@JsonInclude(JsonInclude.Include.ALWAYS) VoteChoice choice) {
}
