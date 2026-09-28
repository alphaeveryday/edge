package com.edge.app.watch.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.IdClass;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.io.Serializable;

@Entity
@Getter
@IdClass(WatchItem.Key.class)
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class WatchItem {
    public record Key(Long groupId, String etfCode) implements Serializable {
    }

    @Id
    @Column(name = "group_id")
    private Long groupId;

    @Id
    @Column(name = "etf_code", length = 6)
    private String etfCode;

    @Column(nullable = false)
    private short position;

    public static WatchItem of(long groupId, String etfCode, int position) {
        WatchItem item = new WatchItem();
        item.groupId = groupId;
        item.etfCode = etfCode;
        item.position = (short) position;
        return item;
    }
}
