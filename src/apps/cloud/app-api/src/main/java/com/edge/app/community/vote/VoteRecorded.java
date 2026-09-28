package com.edge.app.community.vote;


public record VoteRecorded(String etfCode, Long memberId, VoteChoice choice) {
}
