package com.edge.app.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

@Entity
@Table(name = "forecast_vote")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Vote {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "forecast_id", nullable = false)
    private Long forecastId;

    @Column(name = "user_id", nullable = false)
    private Long userId;

    @Enumerated(EnumType.STRING)
    @JdbcTypeCode(SqlTypes.VARCHAR)
    @Column(length = 10, nullable = false)
    private VoteChoice choice;

    public static Vote init(Long forecastId, Long userId, VoteChoice choice) {
        Vote vote = new Vote();
        vote.forecastId = forecastId;
        vote.userId = userId;
        vote.choice = choice;
        return vote;
    }
}
