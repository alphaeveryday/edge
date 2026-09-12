package com.edge.app.entity;

import java.io.Serializable;
import java.util.Objects;

public class VoteId implements Serializable {

	private Long forecastId;
	private Long userId;

	public VoteId() {
	}

	public VoteId(Long forecastId, Long userId) {
		this.forecastId = forecastId;
		this.userId = userId;
	}

	@Override
	public boolean equals(Object o) {
		return o instanceof VoteId other
				&& Objects.equals(forecastId, other.forecastId)
				&& Objects.equals(userId, other.userId);
	}

	@Override
	public int hashCode() {
		return Objects.hash(forecastId, userId);
	}
}
