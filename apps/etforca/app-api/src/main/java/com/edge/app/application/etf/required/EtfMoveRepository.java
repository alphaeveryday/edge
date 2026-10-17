package com.edge.app.application.etf.required;

import com.edge.app.domain.etf.EtfMove;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface EtfMoveRepository extends JpaRepository<EtfMove, Long> {
    Optional<EtfMove> findTopByEtfCodeOrderByAsOfDesc(String etfCode);
}
