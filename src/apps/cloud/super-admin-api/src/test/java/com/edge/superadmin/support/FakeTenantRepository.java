package com.edge.superadmin.support;

import com.edge.superadmin.entity.Tenant;
import com.edge.superadmin.repository.TenantRepository;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/**
 * standalone 컨트롤러·필터 테스트용 in-memory TenantRepository 페이크(레포 hand-fake 관례,
 * Mockito 미도입). 좁은 리포 표면(findAll·save)만 구현한다. save 는 synthetic id 를 부여해
 * 응답 id 매핑을 실 DB 처럼 흉내내고, 목록은 최신(id 내림차순)순으로 낸다.
 */
public class FakeTenantRepository implements TenantRepository {

	private final List<Tenant> store = new ArrayList<>();
	private long nextId = 1;

	public FakeTenantRepository(Tenant... seed) {
		for (Tenant t : seed) {
			store.add(t);
			nextId = Math.max(nextId, t.getId() + 1);
		}
	}

	@Override
	public List<Tenant> findAllByOrderByIdDesc() {
		return store.stream()
				.sorted(Comparator.comparingLong(Tenant::getId).reversed())
				.toList();
	}

	@Override
	public Tenant save(Tenant tenant) {
		Tenant persisted = new Tenant(nextId++, tenant.getName(), tenant.getEnv(),
				tenant.getStatus(),
				tenant.getCreatedAt() == null ? OffsetDateTime.now() : tenant.getCreatedAt());
		store.add(persisted);
		return persisted;
	}
}
