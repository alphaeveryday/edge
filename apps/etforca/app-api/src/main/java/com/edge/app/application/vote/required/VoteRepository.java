package com.edge.app.application.vote.required;

import com.edge.app.domain.vote.Vote;
import com.edge.app.domain.vote.VoteChoice;

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

    interface ReconcileRow {
        String getEtfCode();
        Vote getVote();
    }

    // 투표 0건의 등록 ETF와 선별 목록에서 제외된 ETF의 잔존 투표를 함께 읽는다.
    @Query("""
            select e.code as etfCode, v as vote from Etf e
            left join Vote v on v.etfCode = e.code
            union all
            select v.etfCode as etfCode, v as vote from Vote v
            where not exists (select e.code from Etf e where e.code = v.etfCode)
            """)
    List<ReconcileRow> findReconcileRows();

    // ETF 존재 확인과 upsert를 한 문장으로 처리한다.
    // 커밋까지 ETF 삭제를 막되 다른 회원의 투표는 허용하는 KEY SHARE 잠금.
    @Modifying
    @Query(value = """
            insert into vote(etf_code, member_id, choice)
            select e.code, :member, :choice from etf e where e.code = :etf for key share of e
            on conflict (etf_code, member_id) do update set choice = excluded.choice
            """, nativeQuery = true)
    int upsert(@Param("etf") String etfCode, @Param("member") Long memberId, @Param("choice") String choice);

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
    @Query("delete from Vote v where v.etfCode = :etf and v.memberId = :member")
    void deleteByEtfCodeAndMemberId(@Param("etf") String etfCode, @Param("member") Long memberId);

    @Modifying
    @Query("delete from Vote v where v.memberId = :member")
    void deleteByMember(@Param("member") long memberId);
}
