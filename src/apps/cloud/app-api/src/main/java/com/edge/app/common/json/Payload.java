package com.edge.app.common.json;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.util.List;
import java.util.stream.StreamSupport;

/** 발행본 원문 payload 읽기. 없는 키는 빈 값 */
public record Payload(JsonNode node) {
    private static final JsonMapper MAPPER = JsonMapper.builder().build();

    public static Payload parse(String json) {
        return new Payload(json == null ? MAPPER.nullNode() : MAPPER.readTree(json));
    }

    public Payload get(String name) {
        return new Payload(node.path(name));
    }

    public boolean isNull() {
        return node.isNull() || node.isMissingNode();
    }

    public String text(String name) {
        JsonNode child = node.path(name);
        return child.isValueNode() && !child.isNull() ? child.asText() : "";
    }

    public double number(String name) {
        return node.path(name).asDouble(0);
    }

    public List<Payload> list(String name) {
        return StreamSupport.stream(node.path(name).spliterator(), false).map(Payload::new).toList();
    }

    /** 문자열 배열 노드 읽기 */
    public List<String> strings() {
        return StreamSupport.stream(node.spliterator(), false).map(n -> n.asText("")).toList();
    }

    public List<String> texts(String name) {
        return StreamSupport.stream(node.path(name).spliterator(), false)
                .map(n -> n.isValueNode() ? n.asText() : n.path("sentence").asText("")).toList();
    }

    public <T> T as(Class<T> type) {
        return MAPPER.treeToValue(node, type);
    }
}
