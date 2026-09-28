package com.edge.app.common.cursor;

import com.edge.common.exception.GeneralException;
import org.junit.jupiter.api.Test;

import java.time.Instant;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class CursorTests {
    @Test
    void roundTripKeepsMicrosecondPrecision() {
        // PostgreSQL timestamptz 는 마이크로초까지라 그 정밀도가 남아야 같은 행을 다시 가리킨다.
        Cursor cursor = new Cursor(Instant.parse("2026-09-28T01:02:03.123456Z"), 42L);
        assertEquals(cursor, Cursor.decode(cursor.encode()));
    }

    @Test
    void malformedCursorIsBadRequest() {
        for (String bad : new String[] {"", "!!!", "bm9jb2xvbg", "MTIzOmFiYw"}) {
            GeneralException e = assertThrows(GeneralException.class, () -> Cursor.decode(bad));
            assertEquals("COMMON400", e.getErrorReason().getCode());
        }
    }
}
