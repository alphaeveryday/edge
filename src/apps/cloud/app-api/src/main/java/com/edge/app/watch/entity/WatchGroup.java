package com.edge.app.watch.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Entity
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class WatchGroup {
    public static final String BASE_KEY = "base";

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "principal_id", nullable = false)
    private Long principalId;

    @Column(name = "\"key\"", length = 20, nullable = false)
    private String key;

    @Column(length = 30, nullable = false)
    private String label;

    @Column(nullable = false)
    private short position;

    @Column(name = "is_default", nullable = false)
    private boolean isDefault;

    public static WatchGroup base(long principalId) {
        return create(principalId, BASE_KEY, "기본 관심", 0, true);
    }

    public static WatchGroup user(long principalId, String key, String label, int position) {
        return create(principalId, key, label, position, false);
    }

    private static WatchGroup create(long principalId, String key, String label, int position, boolean isDefault) {
        WatchGroup group = new WatchGroup();
        group.principalId = principalId;
        group.key = key;
        group.label = label;
        group.position = (short) position;
        group.isDefault = isDefault;
        return group;
    }
}
