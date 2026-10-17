package com.edge.app.application.home.required;

import com.edge.app.domain.home.MarketIndex;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface MarketIndexRepository extends JpaRepository<MarketIndex, String> {
    List<MarketIndex> findAllByOrderByCode();
}
