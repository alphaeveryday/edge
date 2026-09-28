package com.edge.app.etf.repository;

import com.edge.app.etf.entity.EtfCandle;
import org.springframework.data.jpa.repository.JpaRepository;

import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

public interface EtfCandleRepository extends JpaRepository<EtfCandle, EtfCandle.Key> {
    Optional<EtfCandle> findTopByEtfCodeOrderByTradeDateDesc(String etfCode);

    List<EtfCandle> findByEtfCodeAndTradeDateGreaterThanEqualOrderByTradeDate(String etfCode, LocalDate from);

    /** 이동평균용 선행 구간 조회. ma20 은 19행 선행 */
    List<EtfCandle> findTop19ByEtfCodeAndTradeDateLessThanOrderByTradeDateDesc(String etfCode, LocalDate before);
}
