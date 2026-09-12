package com.edge.app.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.Instant;

@Entity
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Forecast {

	@Id
	@GeneratedValue(strategy = GenerationType.IDENTITY)
	private Long id;

	private String ticker;

	@Enumerated(EnumType.STRING)
	private Direction direction;

	private String rationale;

	@Enumerated(EnumType.STRING)
	private ForecastStatus status;

	private Instant endAt;

	private String withdrawReason;

	private Instant createdAt;

	public Forecast(String ticker, Direction direction, String rationale, Instant endAt) {
		this.ticker = ticker;
		this.direction = direction;
		this.rationale = rationale;
		this.status = ForecastStatus.OPEN;
		this.endAt = endAt;
		this.createdAt = Instant.now();
	}
}
