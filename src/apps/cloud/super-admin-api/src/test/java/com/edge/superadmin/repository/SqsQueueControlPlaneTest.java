package com.edge.superadmin.repository;

import org.junit.jupiter.api.Test;
import software.amazon.awssdk.services.sqs.SqsClient;
import software.amazon.awssdk.services.sqs.model.GetQueueAttributesRequest;
import software.amazon.awssdk.services.sqs.model.GetQueueAttributesResponse;
import software.amazon.awssdk.services.sqs.model.QueueAttributeName;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class SqsQueueControlPlaneTest {

	@Test
	void 원큐와_DLQ의_현재_깊이를_한_행으로_관측한다() {
		SqsClient sqs = mock(SqsClient.class);
		when(sqs.getQueueAttributes(any(GetQueueAttributesRequest.class)))
				.thenReturn(response(7, 3, 5), response(2, 4, 6));
		var catalog = new QueueControlPlane.QueueCatalog(Map.of("news", "queue-url"),
				Map.of("news", "dlq-url"));

		assertThat(new SqsQueueControlPlane(sqs, catalog).observe())
				.containsExactly(new QueueControlPlane.QueueState("news", 7, 3, 12));
	}

	@Test
	void 한_큐라도_조회하지_못하면_부분_관측을_내보내지_않는다() {
		SqsClient sqs = mock(SqsClient.class);
		when(sqs.getQueueAttributes(any(GetQueueAttributesRequest.class)))
				.thenThrow(new RuntimeException("denied"));
		var catalog = new QueueControlPlane.QueueCatalog(Map.of("news", "queue-url"),
				Map.of("news", "dlq-url"));

		assertThat(new SqsQueueControlPlane(sqs, catalog).observe()).isNull();
	}

	@Test
	void 큐_설정이_없으면_빈_실측으로_위조하지_않는다() {
		var catalog = new QueueControlPlane.QueueCatalog(Map.of(), Map.of());
		assertThat(new SqsQueueControlPlane(mock(SqsClient.class), catalog).observe()).isNull();
	}

	private static GetQueueAttributesResponse response(long visible, long inFlight, long delayed) {
		return GetQueueAttributesResponse.builder().attributes(Map.of(
				QueueAttributeName.APPROXIMATE_NUMBER_OF_MESSAGES, Long.toString(visible),
				QueueAttributeName.APPROXIMATE_NUMBER_OF_MESSAGES_NOT_VISIBLE, Long.toString(inFlight),
				QueueAttributeName.APPROXIMATE_NUMBER_OF_MESSAGES_DELAYED, Long.toString(delayed)))
				.build();
	}
}
