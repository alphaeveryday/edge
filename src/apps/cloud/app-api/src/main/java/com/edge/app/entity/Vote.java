package com.edge.app.entity;

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
public class Vote {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "etf_code", length = 6, nullable = false)
    private String etfCode;

    @Column(name = "member_id", nullable = false)
    private Long memberId;

    @Column(length = 5, nullable = false)
    private VoteChoice choice;

    public static Vote init(String etfCode, Long memberId, VoteChoice choice) {
        Vote vote = new Vote();
        vote.etfCode = etfCode;
        vote.memberId = memberId;
        vote.choice = choice;
        return vote;
    }
}
