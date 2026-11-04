package com.edge.app.batch.sync;

import tools.jackson.databind.json.JsonMapper;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalTime;
import java.time.ZoneId;
import java.util.LinkedHashMap;
import java.util.Map;

/** 동기화 step 들이 함께 쓰는 시각 기준과 값 표기 */
final class SyncFormat {
    static final ZoneId KST = ZoneId.of("Asia/Seoul");
    static final LocalTime MARKET_CLOSE = LocalTime.of(15, 30);
    static final JsonMapper JSON = JsonMapper.builder().build();

    private SyncFormat() {
    }

    static BigDecimal pct(BigDecimal prev, BigDecimal last) {
        if (prev == null || last == null || prev.signum() == 0) {
            return BigDecimal.ZERO;
        }
        return last.divide(prev, 8, RoundingMode.HALF_UP).subtract(BigDecimal.ONE).movePointRight(2).setScale(2, RoundingMode.HALF_UP);
    }

    static Map<String, Object> map(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) {
            m.put((String) kv[i], kv[i + 1]);
        }
        return m;
    }
}
