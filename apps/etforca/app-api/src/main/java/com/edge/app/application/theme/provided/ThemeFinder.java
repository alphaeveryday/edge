package com.edge.app.application.theme.provided;

import java.util.List;

/**
 * 테마 목록 조회
 */
public interface ThemeFinder {
    List<ThemeResponse> list();
}
