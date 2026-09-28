package com.edge.app.community.vote;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface VoteRepository extends JpaRepository<Vote, Long> {
    interface ChoiceCount {
        VoteChoice getChoice();
        long getTotal();
    }

    // 신규/변경을 DB 원자 upsert 로 판정한다 — SELECT 선검사는 동시 요청 레이스가 있다.
    @Modifying
    @Query(value = """
            insert into vote(etf_code, member_id, choice) values (:etf, :member, :choice)
            on conflict (etf_code, member_id) do update set choice = excluded.choice
            """, nativeQuery = true)
    void upsert(@Param("etf") String etfCode, @Param("member") Long memberId, @Param("choice") String choice);

    @Query("""
            select v.choice as choice, count(v) as total from Vote v
            where v.etfCode = :etf group by v.choice
            """)
    List<ChoiceCount> countByChoice(@Param("etf") String etfCode);
}
