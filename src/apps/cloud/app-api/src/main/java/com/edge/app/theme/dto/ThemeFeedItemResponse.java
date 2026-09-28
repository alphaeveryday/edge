package com.edge.app.theme.dto;

import com.edge.app.etf.entity.Dir;

public record ThemeFeedItemResponse(String key, int count, String headline, Dir dir) {
}
