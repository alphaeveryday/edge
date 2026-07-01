package com.edge.gateway.analysis;

import java.util.List;
import java.util.Locale;

import org.springframework.stereotype.Component;

import com.edge.gateway.tenant.TenantContext;

/**
 * mock 분석 클라이언트 — 실제 내부 분석 API(S046·S049) 대신 in-process 고정 fixture를 반환한다.
 *
 * <p>스텁이라 4상태(success/empty/error/fallback)를 <b>심볼로 구동</b>한다:
 * {@code EMPTY}→영향자산 없음, {@code ERROR}→예외, {@code FALLBACK}→stale, 그 외→success.
 * 이 심볼 훅은 얕은 E2E 테스트용이며 M2 실연동 시 제거된다.
 */
@Component
public class MockAnalysisClient {

    /** 스텁: 결정적 테스트를 위해 고정 시각. */
    private static final String AS_OF = "2026-03-12T15:30:00+09:00";
    private static final String FALLBACK_BASED_AT = "2026-03-12T14:45:00+09:00";

    public AnalysisResponse analyze(TenantContext tenant, String symbol) {
        if (symbol == null || symbol.isBlank()) {
            throw new IllegalArgumentException("symbol is required");
        }
        String key = symbol.trim().toUpperCase(Locale.ROOT);
        return switch (key) {
            case "ERROR" -> throw new IllegalStateException("mock analysis failure");
            case "EMPTY" -> new AnalysisResponse(reqId(symbol), AS_OF, List.of(), false, null, null);
            case "FALLBACK" -> new AnalysisResponse(reqId(symbol), null,
                    List.of(new AffectedAsset(symbol, summaryFor(symbol))),
                    true, "실시간 분석 데이터를 수집할 수 없습니다.", FALLBACK_BASED_AT);
            default -> new AnalysisResponse(reqId(symbol), AS_OF,
                    List.of(new AffectedAsset(symbol, summaryFor(symbol))), false, null, null);
        };
    }

    private static String reqId(String symbol) {
        return "req_stub_" + symbol.trim();
    }

    private static String summaryFor(String symbol) {
        return "이 종목(" + symbol.trim() + ")의 최근 가격 변동은 관련 뉴스가 영향을 준 것으로 보입니다. (게이트웨이 스텁 응답)";
    }
}
