package com.edge.app.application.explore;

import com.edge.app.application.etf.provided.EtfSummaryResponse;
import com.edge.app.application.etf.required.EtfRepository;
import com.edge.app.application.explore.provided.RankRowResponse;
import com.edge.app.application.explore.required.EtfRankRepository;
import com.edge.app.domain.explore.EtfRank;
import com.edge.app.support.json.Payload;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * 최신 as_of 순위 조회
 * 동기화에 없는 ETF 숨김
 */
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
