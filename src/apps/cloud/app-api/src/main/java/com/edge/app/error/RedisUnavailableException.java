package com.edge.app.error;

public class RedisUnavailableException extends RuntimeException {

	public RedisUnavailableException(Throwable cause) {
		super(cause);
	}
}
