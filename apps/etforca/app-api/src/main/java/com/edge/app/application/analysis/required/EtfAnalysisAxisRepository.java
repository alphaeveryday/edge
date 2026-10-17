package com.edge.app.application.analysis.required;

import com.edge.app.domain.analysis.EtfAnalysisAxis;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface EtfAnalysisAxisRepository extends JpaRepository<EtfAnalysisAxis, EtfAnalysisAxis.Key> {
    @Query("select x.axis from EtfAnalysisAxis x where x.etfAnalysisId = :id")
    List<String> axesOf(@Param("id") long etfAnalysisId);
}
