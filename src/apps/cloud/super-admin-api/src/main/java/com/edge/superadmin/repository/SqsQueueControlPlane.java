package com.edge.superadmin.repository;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Repository;
import software.amazon.awssdk.services.sqs.SqsClient;
import software.amazon.awssdk.services.sqs.model.GetQueueAttributesRequest;
import software.amazon.awssdk.services.sqs.model.QueueAttributeName;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** {@link QueueControlPlane}의 SQS 구현. 일부 성공을 전체 관측처럼 내보내지 않는다. */
@Repository
public class SqsQueueControlPlane implements QueueControlPlane {

	private static final Logger log = LoggerFactory.getLogger(SqsQueueControlPlane.class);
	private static final List<QueueAttributeName> DEPTH = List.of(
			QueueAttributeName.APPROXIMATE_NUMBER_OF_MESSAGES,
			QueueAttributeName.APPROXIMATE_NUMBER_OF_MESSAGES_NOT_VISIBLE,
			QueueAttributeName.APPROXIMATE_NUMBER_OF_MESSAGES_DELAYED);

	private final SqsClient sqs;
	private final QueueCatalog catalog;

	public SqsQueueControlPlane(SqsClient sqs, QueueCatalog catalog) {
		this.sqs = sqs;
		this.catalog = catalog;
	}

	@Override
	public List<QueueState> observe() {
		if (catalog.queueUrls().isEmpty()) {
			return null;
		}
		List<QueueState> states = new ArrayList<>();
		for (String name : catalog.queueUrls().keySet().stream().sorted().toList()) {
			try {
				Map<QueueAttributeName, String> queue = attributes(catalog.queueUrls().get(name));
				Map<QueueAttributeName, String> dlq = attributes(catalog.dlqUrls().get(name));
				states.add(new QueueState(name, count(queue, QueueAttributeName.APPROXIMATE_NUMBER_OF_MESSAGES),
						count(queue, QueueAttributeName.APPROXIMATE_NUMBER_OF_MESSAGES_NOT_VISIBLE),
						depth(dlq)));
			}
			catch (RuntimeException e) {
				log.warn("SQS 제어면 조회 실패 — queues 축 전체를 비운다 (queue={})", name, e);
				return null;
			}
		}
		return List.copyOf(states);
	}

	private Map<QueueAttributeName, String> attributes(String url) {
		return sqs.getQueueAttributes(GetQueueAttributesRequest.builder()
				.queueUrl(url).attributeNames(DEPTH).build()).attributes();
	}

	private static long depth(Map<QueueAttributeName, String> attributes) {
		return DEPTH.stream().mapToLong(name -> count(attributes, name)).sum();
	}

	private static long count(Map<QueueAttributeName, String> attributes, QueueAttributeName name) {
		String value = attributes.get(name);
		if (value == null) throw new IllegalStateException("SQS 응답에 " + name + " 속성이 없다");
		return Long.parseLong(value);
	}
}
