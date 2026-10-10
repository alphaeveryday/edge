package com.edge.app.notification.service;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * APNs·FCM 을 대신 보내는 Expo Push API 호출
 * 실패는 로그만 남기고 반환은 Expo 가 무효라고 한 토큰
 */
@Slf4j
@Service
public class ExpoPushClient {
    private static final int BATCH = 100;

    private final RestClient http;

    @Autowired
    public ExpoPushClient() {
        this(RestClient.create("https://exp.host"));
    }

    ExpoPushClient(RestClient http) {
        this.http = http;
    }

    public List<String> send(List<String> tokens, String title, String body, Map<String, String> data) {
        List<String> dead = new ArrayList<>();
        for (int from = 0; from < tokens.size(); from += BATCH) {
            List<String> batch = tokens.subList(from, Math.min(from + BATCH, tokens.size()));
            List<Map<String, Object>> messages = batch.stream()
                    .map(to -> Map.<String, Object>of("to", to, "title", title, "body", body, "data", data, "sound", "default"))
                    .toList();
            try {
                Map<?, ?> response = http.post().uri("/--/api/v2/push/send").contentType(MediaType.APPLICATION_JSON)
                        .body(messages).retrieve().body(Map.class);
                List<?> tickets = response == null || !(response.get("data") instanceof List<?> list) ? List.of() : list;
                for (int i = 0; i < tickets.size() && i < batch.size(); i++) {
                    if (tickets.get(i) instanceof Map<?, ?> ticket && "error".equals(ticket.get("status"))) {
                        if (ticket.get("details") instanceof Map<?, ?> details && "DeviceNotRegistered".equals(details.get("error"))) {
                            dead.add(batch.get(i));
                        } else {
                            log.warn("push ticket error message={}", ticket.get("message"));
                        }
                    }
                }
            } catch (RuntimeException e) {
                log.warn("push send failed count={}", batch.size(), e);
            }
        }
        return dead;
    }
}
