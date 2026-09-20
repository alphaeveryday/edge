package com.edge.app.event;

import com.edge.app.entity.VoteChoice;

public record VoteRecorded(Long forecastId, Long userId, VoteChoice choice) {
}
