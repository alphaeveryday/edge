package com.edge.app.application.theme;

import com.edge.app.application.theme.provided.ThemeFinder;
import com.edge.app.application.theme.provided.ThemeResponse;
import com.edge.app.application.theme.required.ThemeRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Service
@RequiredArgsConstructor
public class ThemeService implements ThemeFinder {
    private final ThemeRepository themeRepository;

    @Override
    @Transactional(readOnly = true)
    public List<ThemeResponse> list() {
        return themeRepository.findAllByOrderByPosition().stream()
                .map(t -> new ThemeResponse(t.getKey(), t.getLabel(), t.getGroup(), t.isHot())).toList();
    }
}
