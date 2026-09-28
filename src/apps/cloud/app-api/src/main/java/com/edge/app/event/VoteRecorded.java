package com.edge.app.event;

import com.edge.app.entity.VoteChoice;

public record VoteRecorded(String etfCode, Long memberId, VoteChoice choice) {
}
