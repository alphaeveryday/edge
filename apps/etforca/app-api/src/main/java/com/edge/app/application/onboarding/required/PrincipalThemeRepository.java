package com.edge.app.application.onboarding.required;

import com.edge.app.domain.onboarding.PrincipalTheme;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface PrincipalThemeRepository extends JpaRepository<PrincipalTheme, PrincipalTheme.Key> {
    List<PrincipalTheme> findByPrincipalId(long principalId);

    void deleteByPrincipalId(long principalId);
}
