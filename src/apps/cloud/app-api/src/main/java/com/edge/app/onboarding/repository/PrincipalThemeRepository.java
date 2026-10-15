package com.edge.app.onboarding.repository;

import com.edge.app.onboarding.entity.PrincipalTheme;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface PrincipalThemeRepository extends JpaRepository<PrincipalTheme, PrincipalTheme.Key> {
    List<PrincipalTheme> findByPrincipalId(long principalId);

    void deleteByPrincipalId(long principalId);
}
