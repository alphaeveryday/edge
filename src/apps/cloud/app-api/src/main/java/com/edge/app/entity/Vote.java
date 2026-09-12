package com.edge.app.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.IdClass;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.Instant;

@Entity
@IdClass(VoteId.class)
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Vote {

	@Id
	private Long forecastId;

	@Id
	private Long userId;

	@Enumerated(EnumType.STRING)
	private VoteChoice choice;

	private Instant updatedAt;
}
