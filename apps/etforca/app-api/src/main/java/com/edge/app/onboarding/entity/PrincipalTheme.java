package com.edge.app.onboarding.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.IdClass;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/** 온보딩 선택 테마 */
@Entity
@Getter
@IdClass(PrincipalTheme.Key.class)
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class PrincipalTheme {
    public record Key(Long principalId, String themeKey) implements Serializable {
    }

    @Id
    @Column(name = "principal_id")
    private Long principalId;

    @Id
    @Column(name = "theme_key", length = 30)
    private String themeKey;

    public static PrincipalTheme of(long principalId, String themeKey) {
        PrincipalTheme theme = new PrincipalTheme();
        theme.principalId = principalId;
        theme.themeKey = themeKey;
        return theme;
    }
}
