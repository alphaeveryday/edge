package com.edge.app.application.etf.provided;

import com.edge.app.domain.etf.Dir;

import java.time.Instant;
import java.util.List;

public record MoveResponse(Instant at, String text, String foot, String sheetTitle, List<Group> groups) {
    public record Group(String head, List<Item> items) {
    }

    public record Item(Dir dir, String t, String sub) {
    }
}
