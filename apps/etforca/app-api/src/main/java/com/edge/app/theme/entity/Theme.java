package com.edge.app.theme.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Entity
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Theme {
    @Id
    @Column(name = "\"key\"", length = 30)
    private String key;

    @Column(length = 30, nullable = false)
    private String label;

    @Column(name = "\"group\"", length = 10, nullable = false)
    private String group;

    @Column(nullable = false)
    private boolean hot;

    @Column(nullable = false)
    private short position;
}
