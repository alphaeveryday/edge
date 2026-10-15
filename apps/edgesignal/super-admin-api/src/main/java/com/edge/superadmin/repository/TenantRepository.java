package com.edge.superadmin.repository;

import com.edge.superadmin.entity.Tenant;
import org.springframework.data.repository.Repository;

import java.util.List;

/**
 * tenant 리포지토리 — 이 모듈이 실제로 쓰는 연산만 노출한다(read=findAll, write=save).
 * JpaRepository 전체 CRUD 표면 대신 좁은 {@link Repository} 로 두어 표면을 최소화하고,
 * standalone 테스트에서 손 페이크로 대체 가능하게 한다(레포의 hand-fake 관례, Mockito 미도입).
 * 목록은 최신(id 내림차순)순 — 신규 테넌트가 화면 맨 앞에 오는 UI 계약을 실 쿼리로 보장한다.
 */
public interface TenantRepository extends Repository<Tenant, Long> {

	List<Tenant> findAllByOrderByIdDesc();

	Tenant save(Tenant tenant);
}
