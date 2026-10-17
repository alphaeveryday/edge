package com.edge.app.application.vote.provided;

import com.edge.app.domain.vote.VoteCounts;

public record VoteCountResponse(long buys, long waits, long sells, String source) {

    public static VoteCountResponse from(VoteCounts counts, String source) {
        return new VoteCountResponse(counts.buys(), counts.waits(), counts.sells(), source);
    }
}
