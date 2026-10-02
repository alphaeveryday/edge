package com.edge.tenantsync.dto;

/** 문서와 계산만 허용하는 전달 근거. 각 형상의 JSON 필드는 해당 DTO가 소유한다. */
public sealed interface BundleEvidence permits EvidenceItem, CalculationEvidenceItem {
}
