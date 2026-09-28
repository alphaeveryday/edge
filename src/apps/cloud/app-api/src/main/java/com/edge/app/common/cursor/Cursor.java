package com.edge.app.common.cursor;

import com.edge.common.apipayload.code.status.ErrorStatus;
import com.edge.common.exception.GeneralException;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;

/**
 * 키셋 커서 (created_at DESC, id DESC). 문자열 형식은 base64url("epochMicros:id").
 * 서비스는 마지막 행으로 만들어 nextCursor 로 주고, 다음 요청의 cursor 를 decode 해 WHERE 에 쓴다.
 */
public record Cursor(Instant createdAt, long id) {
    private static final long MICROS = 1_000_000L;

    public String encode() {
        long micros = createdAt.getEpochSecond() * MICROS + createdAt.getNano() / 1_000;
        return Base64.getUrlEncoder().withoutPadding()
                .encodeToString((micros + ":" + id).getBytes(StandardCharsets.US_ASCII));
    }

    /** 형식이 어긋나면 COMMON400. */
    public static Cursor decode(String encoded) {
        try {
            String raw = new String(Base64.getUrlDecoder().decode(encoded), StandardCharsets.US_ASCII);
            int sep = raw.indexOf(':');
            if (sep < 0) {
                throw new GeneralException(ErrorStatus._BAD_REQUEST);
            }
            long micros = Long.parseLong(raw.substring(0, sep));
            long id = Long.parseLong(raw.substring(sep + 1));
            return new Cursor(Instant.ofEpochSecond(Math.floorDiv(micros, MICROS),
                    Math.floorMod(micros, MICROS) * 1_000), id);
        } catch (IllegalArgumentException e) {
            throw new GeneralException(ErrorStatus._BAD_REQUEST);
        }
    }
}
