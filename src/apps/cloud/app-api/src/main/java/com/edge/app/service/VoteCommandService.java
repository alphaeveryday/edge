package com.edge.app.service;

import com.edge.app.entity.VoteChoice;

public interface VoteCommandService {
    void vote(Long forecastId, Long userId, VoteChoice choice);
}
