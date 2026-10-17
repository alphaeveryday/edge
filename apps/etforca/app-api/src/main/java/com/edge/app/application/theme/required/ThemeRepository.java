package com.edge.app.application.theme.required;

import com.edge.app.domain.theme.Theme;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface ThemeRepository extends JpaRepository<Theme, String> {
    List<Theme> findAllByOrderByPosition();
}
