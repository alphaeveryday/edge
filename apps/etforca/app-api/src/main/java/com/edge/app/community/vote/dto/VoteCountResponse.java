package com.edge.app.community.vote.dto;

import com.edge.app.community.vote.repository.VoteCounts;

public record VoteCountResponse(long buys, long waits, long sells, String source) {

    public static VoteCountResponse from(VoteCounts counts, String source) {
        return new VoteCountResponse(counts.buys(), counts.waits(), counts.sells(), source);
    }
}
