package com.edge.app.member.dto;

import jakarta.validation.constraints.Size;

public record MeUpdateRequest(@Size(min = 1, max = 30) String nick, @Size(min = 2, max = 30) String handle) {
}
