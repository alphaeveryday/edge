package com.edge.app.community.vote.event;

import com.edge.app.community.vote.entity.VoteChoice;


public record VoteRecorded(String etfCode, Long memberId, VoteChoice choice) {
}
