package com.edge.app.theme.entity;

// 쿼리 파라미터 dir 의 계약값(all | up | down). up=help, down=burden, all 은 neutral 포함 전부.
public enum FeedDir {
    ALL(""), UP("help"), DOWN("burden");

    private final String detailDir;

    FeedDir(String detailDir) {
        this.detailDir = detailDir;
    }

    public String detailDir() {
        return detailDir;
    }

    public static FeedDir of(String value) {
        for (FeedDir dir : values()) {
            if (dir.name().toLowerCase().equals(value)) {
                return dir;
            }
        }
        throw new IllegalArgumentException("unknown feed dir: " + value);
    }
}
