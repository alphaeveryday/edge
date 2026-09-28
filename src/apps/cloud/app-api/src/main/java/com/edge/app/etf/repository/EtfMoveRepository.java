package com.edge.app.etf.repository;

import com.edge.app.etf.entity.EtfMove;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface EtfMoveRepository extends JpaRepository<EtfMove, Long> {
    Optional<EtfMove> findTopByEtfCodeOrderByAsOfDesc(String etfCode);
}
