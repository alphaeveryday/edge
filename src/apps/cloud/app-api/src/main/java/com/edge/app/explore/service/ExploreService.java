package com.edge.app.explore.service;

import com.edge.app.explore.dto.RankRowResponse;
import org.springframework.stereotype.Service;

import java.util.List;

/** 스텁. */
@Service
public class ExploreService {
    public List<RankRowResponse> rank() {
        return List.of(ExploreExamples.rankRow());
    }
}
