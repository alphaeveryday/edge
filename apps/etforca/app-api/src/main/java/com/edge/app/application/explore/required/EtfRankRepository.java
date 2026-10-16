package com.edge.app.application.explore.required;

import com.edge.app.domain.explore.EtfRank;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

import java.time.LocalDate;
import java.util.List;

public interface EtfRankRepository extends JpaRepository<EtfRank, EtfRank.Key> {
    @Query("select max(r.asOf) from EtfRank r")
    LocalDate latestAsOf();

    List<EtfRank> findByAsOfOrderByRank(LocalDate asOf);
}
