package com.edge.app.support.cursor;

import com.edge.common.exception.GeneralException;
import org.junit.jupiter.api.Test;

import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class CursorTest {
    @Test
    void roundTripKeepsMicrosecondPrecision() {
        // 같은 행을 다시 가리키기 위한 PostgreSQL timestamptz 의 마이크로초 정밀도 보존
        Cursor cursor = new Cursor(Instant.parse("2026-09-28T01:02:03.123456Z"), 42L);
        assertThat(Cursor.decode(cursor.encode())).isEqualTo(cursor);
    }

    @Test
    void malformedCursorIsBadRequest() {
        for (String bad : new String[] {"", "!!!", "bm9jb2xvbg", "MTIzOmFiYw"}) {
            assertThatThrownBy(() -> Cursor.decode(bad))
                    .isInstanceOfSatisfying(GeneralException.class, e -> assertThat(e.getErrorReason().getCode()).isEqualTo("COMMON400"));
        }
    }
}
