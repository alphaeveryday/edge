package com.edge.app.dto;

public record VoteCountResponse(long buy, long hold, long sell, String source) {

    public static VoteCountResponse from(VoteCounts counts, String source) {
        return new VoteCountResponse(counts.buy(), counts.hold(), counts.sell(), source);
    }
}
