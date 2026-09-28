package com.edge.app.dto;

public record VoteCountResponse(long buys, long waits, long sells, String source) {

    public static VoteCountResponse from(VoteCounts counts, String source) {
        return new VoteCountResponse(counts.buys(), counts.waits(), counts.sells(), source);
    }
}
