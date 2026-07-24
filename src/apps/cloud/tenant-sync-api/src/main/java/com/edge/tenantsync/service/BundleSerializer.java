package com.edge.tenantsync.service;

import com.edge.tenantsync.dto.EventBundle;
import org.springframework.stereotype.Component;
import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.json.JsonMapper;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

/**
 * 번들 → 응답 바이트 + 체크섬. 계약(docs/contracts/event-bundle-schema.md)의 핵심 규율:
 * 체크섬 대상은 "응답 body 바이트열 그대로" — 직렬화를 딱 한 번 하고, 그 바이트로 SHA-256 을
 * 계산하며, 같은 바이트를 body 로 보낸다. 재직렬화하면 필드 순서·공백이 달라져 검증이 깨질 수 있다.
 */
@Component
public class BundleSerializer {

	private final JsonMapper mapper;

	public BundleSerializer() {
		// 필드 표기는 계약 JSON 과 동일하게 snake_case (bundle_id·cursor_from·delivery_type …).
		this.mapper = JsonMapper.builder()
				.propertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)
				.build();
	}

	public SerializedBundle serialize(EventBundle bundle) {
		byte[] body = mapper.writeValueAsBytes(bundle);
		return new SerializedBundle(body, "sha256=" + sha256Hex(body));
	}

	private static String sha256Hex(byte[] body) {
		try {
			return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(body));
		} catch (NoSuchAlgorithmException e) {
			throw new IllegalStateException("SHA-256 미지원 JVM", e);
		}
	}

	/** body 와 checksum 은 같은 바이트에서 나온 쌍 — 따로 만들지 말 것. */
	public record SerializedBundle(byte[] body, String checksum) {
	}
}
