package com.edge.app.repository;

import com.edge.app.entity.Vote;
import com.edge.app.entity.VoteChoice;
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
            insert into forecast_vote(forecast_id, user_id, choice) values (:forecast, :user, :choice)
            as new on duplicate key update choice = new.choice
            """, nativeQuery = true)
    void upsert(@Param("forecast") Long forecastId, @Param("user") Long userId, @Param("choice") String choice);

    @Query("""
            select v.choice as choice, count(v) as total from Vote v
            where v.forecastId = :forecast group by v.choice
            """)
    List<ChoiceCount> countByChoice(@Param("forecast") Long forecastId);
}
