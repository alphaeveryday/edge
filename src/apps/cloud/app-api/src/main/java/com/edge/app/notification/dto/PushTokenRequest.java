package com.edge.app.notification.dto;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;

public record PushTokenRequest(@NotNull @Pattern(regexp = "^Expo(nent)?PushToken\\[[^\\]]{1,200}\\]$") String token) {
}
