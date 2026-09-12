package com.edge.app.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.Instant;

@Entity
@Table(name = "outbox")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class OutboxEvent {

	@Id
	@GeneratedValue(strategy = GenerationType.IDENTITY)
	private Long id;

	private String eventType;

	private Long forecastId;

	private String payload;

	private Instant createdAt;

	private Instant publishedAt;

	private OutboxEvent(String eventType, Long forecastId) {
		this.eventType = eventType;
		this.forecastId = forecastId;
		this.payload = "{\"type\":\"" + eventType + "\",\"forecastId\":" + forecastId + "}";
		this.createdAt = Instant.now();
	}

	public static OutboxEvent published(Long forecastId) {
		return new OutboxEvent("PUBLISHED", forecastId);
	}

	public static OutboxEvent withdrawn(Long forecastId) {
		return new OutboxEvent("WITHDRAWN", forecastId);
	}
}
