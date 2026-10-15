package com.edge.app.home.repository;

import com.edge.app.home.entity.MarketIndex;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface MarketIndexRepository extends JpaRepository<MarketIndex, String> {
    List<MarketIndex> findAllByOrderByCode();
}
