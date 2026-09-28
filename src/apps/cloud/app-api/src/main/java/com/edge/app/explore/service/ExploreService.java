package com.edge.app.explore.service;

import com.edge.app.common.json.Payload;
import com.edge.app.etf.dto.EtfSummaryResponse;
import com.edge.app.etf.repository.EtfRepository;
import com.edge.app.explore.dto.RankRowResponse;
import com.edge.app.explore.entity.EtfRank;
import com.edge.app.explore.repository.EtfRankRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

/** 최신 as_of 의 순위만. 동기화에서 빠진 ETF 행은 숨긴다. */
@Service
@RequiredArgsConstructor
public class ExploreService {
    private final EtfRankRepository rankRepository;
    private final EtfRepository etfRepository;

    @Transactional(readOnly = true)
    public List<RankRowResponse> rank() {
        LocalDate asOf = rankRepository.latestAsOf();
        if (asOf == null) {
            return List.of();
        }
        List<EtfRank> ranks = rankRepository.findByAsOfOrderByRank(asOf);
        if (ranks.isEmpty()) {
            return List.of();
        }
        Map<String, EtfSummaryResponse> byCode = etfRepository.summaries(ranks.stream().map(EtfRank::getEtfCode).toList())
                .stream().map(EtfSummaryResponse::from).collect(Collectors.toMap(EtfSummaryResponse::code, Function.identity()));
        return ranks.stream().filter(r -> byCode.containsKey(r.getEtfCode()))
                .map(r -> new RankRowResponse(byCode.get(r.getEtfCode()), r.getRank(), r.getTitle(),
                        Payload.parse(r.getChips()).strings(), r.isReady()))
                .toList();
    }
}
