package com.edge.app.analysis.repository;

import com.edge.app.analysis.entity.EtfAnalysis;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

public interface EtfAnalysisRepository extends JpaRepository<EtfAnalysis, Long> {
    Optional<EtfAnalysis> findTopByEtfCodeOrderByAsOfDesc(String etfCode);

    Optional<EtfAnalysis> findByEtfCodeAndAsOf(String etfCode, LocalDate asOf);

    /** 직전 발행본 조회 */
    Optional<EtfAnalysis> findTopByEtfCodeAndAsOfLessThanOrderByAsOfDesc(String etfCode, LocalDate asOf);

    Optional<EtfAnalysis> findTopByEtfCodeAndAsOfBetweenOrderByAsOfDesc(String etfCode, LocalDate from, LocalDate to);

    @Query("select a.asOf from EtfAnalysis a where a.etfCode = :code and a.asOf between :from and :to")
    List<LocalDate> asOfsBetween(@Param("code") String etfCode, @Param("from") LocalDate from, @Param("to") LocalDate to);

    @Query("select max(a.asOf) from EtfAnalysis a where a.etfCode = :code and a.asOf < :date")
    LocalDate lastAsOfBefore(@Param("code") String etfCode, @Param("date") LocalDate date);

    @Query("select min(a.asOf) from EtfAnalysis a where a.etfCode = :code and a.asOf > :date")
    LocalDate firstAsOfAfter(@Param("code") String etfCode, @Param("date") LocalDate date);
}
