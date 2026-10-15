package com.edge.app.common.cursor;

import com.edge.common.apipayload.code.status.ErrorStatus;
import com.edge.common.exception.GeneralException;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;

/** created_at·id 쌍을 base64url 로 인코딩한 키셋 커서 */
public record Cursor(Instant createdAt, long id) {
    private static final long MICROS = 1_000_000L;

    public String encode() {
        long micros = createdAt.getEpochSecond() * MICROS + createdAt.getNano() / 1_000;
        return Base64.getUrlEncoder().withoutPadding()
                .encodeToString((micros + ":" + id).getBytes(StandardCharsets.US_ASCII));
    }

    /** 형식 오류의 COMMON400 응답 */
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
