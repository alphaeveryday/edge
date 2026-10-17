package com.edge.app.application.etf.required;

import com.edge.app.domain.etf.EtfDetail;
import org.springframework.data.jpa.repository.JpaRepository;

public interface EtfDetailRepository extends JpaRepository<EtfDetail, String> {
}
