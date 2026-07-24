package com.edge.tenantconsole.mock;

import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;

/**
 * users 도메인 mock 데이터 — 콘솔 사용자·권한 목록. UI 시안(v0.2) 목데이터 이식
 * in-memory 가변 스토어. role 어휘(Admin·Compliance)는 UI 계약을 따른다 — DB 연동
 * (ALPHA-119 사용자 관리) 시 member 원장 어휘(TENANT_ADMIN 등)와 정합을 맞추며
 * service 의 이 스토어 호출부를 repository 로 교체한다(ALPHA-513).
 */
@Component
public class MemberMockStore {

	public record Member(long id, String name, String email, String role, String status,
			String lastLogin) {
	}

	private final List<Member> members = new ArrayList<>(List.of(
			new Member(1, "조영서", "youngseo.cho@kbsec.com", "Admin", "ACTIVE", "오늘 09:12"),
			new Member(2, "박성호", "sungho.park@kbsec.com", "Compliance", "ACTIVE", "오늘 08:47"),
			new Member(3, "이수민", "sumin.lee@kbsec.com", "Admin", "ACTIVE", "어제 17:20"),
			new Member(4, "최다혜", "dahye.choi@kbsec.com", "Compliance", "ACTIVE", "2026-07-08"),
			new Member(5, "정민우", "minwoo.jung@kbsec.com", "Compliance", "INVITED", "—")));
	private long nextId = 6;

	public synchronized List<Member> list() {
		return List.copyOf(members);
	}

	public synchronized void invite(String email, String role) {
		members.add(new Member(nextId++, email.split("@")[0], email, role, "INVITED", "—"));
	}
}
