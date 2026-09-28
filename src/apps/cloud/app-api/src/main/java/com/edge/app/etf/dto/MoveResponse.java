package com.edge.app.etf.dto;

import com.edge.app.etf.entity.Dir;

import java.time.Instant;
import java.util.List;

public record MoveResponse(Instant at, String text, String foot, String sheetTitle, List<Group> groups) {
    public record Group(String head, List<Item> items) {
    }

    public record Item(Dir dir, String t, String sub) {
    }
}
