package com.edge.app.home.service;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.etf.entity.Signal;
import com.edge.app.etf.service.EtfService;
import com.edge.app.home.dto.HomeBriefResponse;
import com.edge.app.watch.service.WatchService;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.List;

/** 스텁. */
@Service
public class HomeService {
    private static final Instant AT = Instant.parse("2026-01-01T00:00:00Z");

    public HomeBriefResponse brief(AppPrincipal principal, String group) {
        return new HomeBriefResponse(AT, List.of(WatchService.BASE), group == null ? WatchService.BASE.key() : group,
                Signal.NEUTRAL, 0, List.of(EtfService.SUMMARY));
    }
}
