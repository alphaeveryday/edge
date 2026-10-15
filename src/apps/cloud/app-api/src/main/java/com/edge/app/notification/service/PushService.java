package com.edge.app.notification.service;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.member.entity.Principal;
import com.edge.app.member.repository.DeviceRepository;
import com.edge.app.member.repository.PrincipalRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Map;

@Service
@RequiredArgsConstructor
public class PushService {
    private final PrincipalRepository principalRepository;
    private final DeviceRepository deviceRepository;
    private final ExpoPushClient expo;

    /** 같은 토큰을 가진 다른 기기에서 떼어 이 기기에 등록 */
    @Transactional
    public void register(AppPrincipal principal, String deviceKey, String token) {
        principalRepository.upsertDevice(deviceKey);
        deviceRepository.releasePushToken(deviceKey, token);
        deviceRepository.registerPush(deviceKey, token, principal.isMember() ? principal.memberId() : null);
    }

    // 발송 중 커넥션을 잡지 않도록 트랜잭션 없이 조회·발송·정리
    public void send(long principalId, String title, String body, Map<String, String> data) {
        Principal principal = principalRepository.findById(principalId).orElse(null);
        if (principal == null) {
            return;
        }
        List<String> tokens = principal.getMemberId() != null
                ? deviceRepository.memberPushTokens(principal.getMemberId())
                : deviceRepository.devicePushTokens(principal.getDeviceId());
        if (tokens.isEmpty()) {
            return;
        }
        List<String> dead = expo.send(tokens, title, body, data);
        if (!dead.isEmpty()) {
            deviceRepository.dropPushTokens(dead);
        }
    }
}
