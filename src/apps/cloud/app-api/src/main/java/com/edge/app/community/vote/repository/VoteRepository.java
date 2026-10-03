package com.edge.app.community.vote.repository;

import com.edge.app.community.vote.entity.Vote;
import com.edge.app.community.vote.entity.VoteChoice;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;

public interface VoteRepository extends JpaRepository<Vote, Long> {
    interface ChoiceCount {
        VoteChoice getChoice();
        long getTotal();
    }

    // DB 원자 upsert 기반의 신규와 변경 판정
    // 동시 요청 레이스가 있는 SELECT 선검사 회피
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

    List<Vote> findByEtfCode(String etfCode);

    Optional<Vote> findByEtfCodeAndMemberId(String etfCode, Long memberId);

    @Query("select distinct v.etfCode from Vote v where v.memberId = :member")
    List<String> etfCodesOf(@Param("member") long memberId);

    @Modifying
    @Query("delete from Vote v where v.memberId = :member")
    void deleteByMember(@Param("member") long memberId);
}
