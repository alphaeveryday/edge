package com.edge.superadmin.repository;

import java.util.List;
import java.util.Map;

/** SQS 제어면 관측(ALPHA-979 조각 3). 원큐와 DLQ의 현재 깊이를 한 축으로 싣는다. */
public interface QueueControlPlane {

	/** {@code null}이면 축 전체 관측 실패 또는 미설정이다. 빈 목록은 설정된 큐를 모두 봤다는 실측이다. */
	List<QueueState> observe();

	record QueueState(String name, long visible, long inFlight, long dlq) { }

	record QueueCatalog(Map<String, String> queueUrls, Map<String, String> dlqUrls) {
		public QueueCatalog {
			queueUrls = Map.copyOf(queueUrls);
			dlqUrls = Map.copyOf(dlqUrls);
			if (!queueUrls.keySet().equals(dlqUrls.keySet())) {
				throw new IllegalArgumentException("원큐와 DLQ 이름 집합이 다르다");
			}
		}
	}
}
