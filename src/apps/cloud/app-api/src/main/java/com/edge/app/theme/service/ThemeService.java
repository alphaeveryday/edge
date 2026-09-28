package com.edge.app.theme.service;

import com.edge.app.theme.dto.ThemeDetailResponse;
import com.edge.app.theme.dto.ThemeFeedItemResponse;
import com.edge.app.theme.dto.ThemeResponse;
import com.edge.app.theme.dto.ThemeSheetResponse;
import org.springframework.stereotype.Service;

import java.util.List;

/** 스텁. */
@Service
public class ThemeService {
    public List<ThemeResponse> list() {
        return List.of(ThemeExamples.theme());
    }

    public List<ThemeFeedItemResponse> feed(String dir) {
        return List.of(ThemeExamples.feedItem());
    }

    public ThemeSheetResponse sheet(String key) {
        return ThemeExamples.sheet(key);
    }

    public ThemeDetailResponse detail(String key) {
        return ThemeExamples.detail(key);
    }
}
