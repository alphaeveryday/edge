package com.edge.app.theme.service;

import com.edge.app.common.AppErrorStatus;
import com.edge.app.common.json.Payload;
import com.edge.app.etf.entity.Dir;
import com.edge.app.theme.dto.ThemeDetailResponse;
import com.edge.app.theme.dto.ThemeFeedItemResponse;
import com.edge.app.theme.dto.ThemeResponse;
import com.edge.app.theme.entity.ThemeDetail;
import com.edge.app.theme.repository.ThemeDetailRepository;
import com.edge.app.theme.repository.ThemeRepository;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Service
@RequiredArgsConstructor
public class ThemeService {
    private final ThemeRepository themeRepository;
    private final ThemeDetailRepository detailRepository;

    @Transactional(readOnly = true)
    public List<ThemeResponse> list() {
        return themeRepository.findAllByOrderByPosition().stream()
                .map(t -> new ThemeResponse(t.getKey(), t.getLabel(), t.getGroup(), t.isHot())).toList();
    }

    @Transactional(readOnly = true)
    public List<ThemeFeedItemResponse> feed() {
        return detailRepository.feed().stream()
                .map(r -> new ThemeFeedItemResponse(r.getKey(), r.getCount().intValue(), r.getHeadline(), Dir.fold(r.getDir())))
                .toList();
    }

    @Transactional(readOnly = true)
    public ThemeDetailResponse detail(String key) {
        requireTheme(key);
        ThemeDetail detail = detailRepository.findTopByThemeKeyOrderByAsOfDesc(key)
                .orElseThrow(() -> new GeneralException(AppErrorStatus.ANALYSIS_NOT_READY));
        Payload p = Payload.parse(detail.getPayload());
        Payload m = p.get("metric");
        var metric = new ThemeDetailResponse.Metric(m.text("name"), m.text("now"), Dir.fold(m.text("dir")),
                m.list("vals").stream().map(v -> v.node().asDouble(0)).toList(), m.number("thresh"),
                m.list("xLabels").stream().map(x -> x.node().asText("")).toList(), m.text("refLabel"), m.text("state"));
        return new ThemeDetailResponse(key, detail.getHeadline(),
                p.list("stocks").stream().map(s -> new ThemeDetailResponse.Stock(s.text("name"), s.text("etfs"))).toList(),
                p.text("intro"), detail.getPublishedAt(), p.text("countLabel"), p.text("todayLine"), p.text("todayEffect"),
                p.text("importantLead"), p.text("importantWhy"), metric, p.text("thesis"), p.text("surface"),
                p.text("structure"), p.text("structureWhy"), p.text("soWhat"));
    }

    private void requireTheme(String key) {
        if (!themeRepository.existsById(key)) {
            throw new GeneralException(AppErrorStatus.THEME_NOT_FOUND);
        }
    }
}
