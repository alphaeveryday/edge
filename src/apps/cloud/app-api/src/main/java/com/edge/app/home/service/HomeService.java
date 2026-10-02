package com.edge.app.home.service;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.etf.dto.EtfSummaryResponse;
import com.edge.app.etf.entity.Signal;
import com.edge.app.etf.repository.EtfRepository;
import com.edge.app.home.dto.HomeBriefResponse;
import com.edge.app.member.repository.PrincipalRepository;
import com.edge.app.watch.dto.WatchGroupResponse;
import com.edge.app.watch.entity.WatchGroup;
import com.edge.app.watch.entity.WatchItem;
import com.edge.app.watch.repository.WatchGroupRepository;
import com.edge.app.watch.repository.WatchItemRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.function.Function;
import java.util.stream.Collectors;

/** 관심 그룹 요약. score 는 signal 서수 평균(소수 둘째 자리), band 는 그 반올림, changePct 는 산술평균. 그룹 없으면 빈 브리프. */
@Service
@RequiredArgsConstructor
public class HomeService {
    private final PrincipalRepository principalRepository;
    private final WatchGroupRepository groupRepository;
    private final WatchItemRepository itemRepository;
    private final EtfRepository etfRepository;

    // principal 해소 upsert 때문에 readOnly 미적용
    @Transactional
    public HomeBriefResponse brief(AppPrincipal principal, String group) {
        long principalId = principalRepository.resolve(principal);
        List<WatchGroup> groups = groupRepository.findByPrincipalIdOrderByPosition(principalId);
        String key = group == null ? WatchGroup.BASE_KEY : group;
        List<String> codes = groups.stream().filter(g -> g.getKey().equals(key)).findFirst()
                .map(g -> itemRepository.findByGroupIdOrderByPosition(g.getId()).stream().map(WatchItem::getEtfCode).toList())
                .orElse(List.of());
        List<EtfSummaryResponse> etfs = summaries(codes);
        Instant asOf = codes.isEmpty() ? null : etfRepository.latestQuoteAsOf(codes);
        double score = score(etfs);
        return new HomeBriefResponse(asOf == null ? Instant.now() : asOf,
                groups.stream().map(g -> new WatchGroupResponse(g.getKey(), g.getLabel(), (int) itemRepository.countByGroupId(g.getId()))).toList(),
                key, Signal.values()[(int) Math.round(score)], score, changePct(etfs), etfs);
    }

    private List<EtfSummaryResponse> summaries(List<String> codes) {
        if (codes.isEmpty()) {
            return List.of();
        }
        Map<String, EtfSummaryResponse> byCode = etfRepository.summaries(codes).stream()
                .map(EtfSummaryResponse::from).collect(Collectors.toMap(EtfSummaryResponse::code, Function.identity()));
        return codes.stream().map(byCode::get).filter(Objects::nonNull).toList();
    }

    // 강력하락 0 ~ 강력상승 4, 그룹이 비면 중립
    private static double score(List<EtfSummaryResponse> etfs) {
        double mean = etfs.stream().mapToInt(e -> e.signal().ordinal()).average().orElse(Signal.NEUTRAL.ordinal());
        return Math.round(mean * 100) / 100.0;
    }

    private static double changePct(List<EtfSummaryResponse> etfs) {
        double mean = etfs.stream().mapToDouble(EtfSummaryResponse::changePct).average().orElse(0);
        return Math.round(mean * 100) / 100.0;
    }
}
