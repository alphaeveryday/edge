package com.edge.app.theme.repository;

import com.edge.app.theme.entity.Theme;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface ThemeRepository extends JpaRepository<Theme, String> {
    List<Theme> findAllByOrderByPosition();
}
