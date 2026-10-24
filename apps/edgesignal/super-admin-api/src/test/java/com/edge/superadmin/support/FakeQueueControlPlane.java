package com.edge.superadmin.support;

import com.edge.superadmin.repository.QueueControlPlane;
import java.util.List;

public class FakeQueueControlPlane implements QueueControlPlane {
	private List<QueueState> states;

	@Override
	public List<QueueState> observe() {
		return states;
	}

	public void returns(List<QueueState> value) {
		states = value;
	}
}
