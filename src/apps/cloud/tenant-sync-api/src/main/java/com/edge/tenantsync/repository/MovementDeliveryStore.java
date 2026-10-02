package com.edge.tenantsync.repository;

import com.edge.tenantsync.dto.BundleEntry;
import com.edge.tenantsync.dto.BundleEvidence;
import com.edge.tenantsync.dto.CalculationEvidenceItem;
import com.edge.tenantsync.dto.EvidenceItem;
import com.edge.tenantsync.dto.ExplanationResult;
import com.edge.tenantsync.dto.ExplanationRun;
import jakarta.persistence.EntityManager;
import org.springframework.stereotype.Repository;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.json.JsonMapper;

import java.time.Instant;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** 발행된 v2 선택 항목과 저장된 계산·뉴스 근거를 기존 전달 계약으로 조립한다. */
@Repository
public class MovementDeliveryStore {
    private final EntityManager entityManager;
    private final JsonMapper json = JsonMapper.builder()
            .enable(DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS).build();

    public MovementDeliveryStore(EntityManager entityManager) {
        this.entityManager = entityManager;
    }

    public record Content(ExplanationResult result, List<BundleEvidence> evidence) {
        BundleEntry entry(long cursor) {
            return BundleEntry.newResult(cursor, result,
                    new ExplanationRun(result.explanationResultId(), null), List.of(), evidence);
        }
    }

    public Map<String, Content> read(Set<String> ids) {
        if (ids.isEmpty()) return Map.of();
        Map<String, JsonNode> analyses = new LinkedHashMap<>();
        for (JsonNode row : query("""
                SELECT (to_jsonb(a) || jsonb_build_object('instrument_id', i.instrument_id,
                    'name', e.display_name))::text
                FROM movement_analyses a
                JOIN instrument i ON i.ticker=a.etf_code AND i.instrument_type='ETF' AND i.market_code='XKRX'
                JOIN entity e ON e.entity_id=i.instrument_id
                WHERE a.analysis_id IN (:ids)
                """, ids)) {
            String id = text(row, "analysis_id");
            require(analyses.put(id, row) == null, "Ambiguous ETF identity");
            require("completed".equals(text(row,"status")) && "database".equals(text(row,"data_source"))
                    && !row.path("published_at").isNull() && row.path("selected_item_ids").size()>0,
                    "Delivery requires a published real-data movement");
        }
        require(analyses.keySet().equals(ids), "Missing movement or ETF identity");
        Map<String, List<BundleEvidence>> evidence = new HashMap<>();
        Set<String> foundItems = new HashSet<>();
        Map<String, JsonNode> evidenceRows = new LinkedHashMap<>();
        Map<String, List<String>> itemsByRun = new HashMap<>();
        for (JsonNode row : query("""
                SELECT jsonb_build_object('analysis_id', a.analysis_id, 'item_id', s.item_id,
                    'owner', to_jsonb(owner), 'item', to_jsonb(i), 'run', to_jsonb(r),
                    'definition', to_jsonb(d), 'run_owner', COALESCE(to_jsonb(m),to_jsonb(o)),
                    'news', COALESCE((SELECT jsonb_object_agg(doc.document_id,to_jsonb(doc))
                        FROM document doc WHERE doc.document_id IN (
                            SELECT n->>'news_id' FROM jsonb_array_elements(
                                COALESCE(r.output->'result'->'news','[]'::jsonb)) n)), '{}'::jsonb))::text
                FROM movement_analyses a
                CROSS JOIN LATERAL unnest(a.selected_item_ids) WITH ORDINALITY s(item_id,position)
                LEFT JOIN movement_items i ON i.item_id=s.item_id
                LEFT JOIN movement_analyses owner ON owner.analysis_id=i.analysis_id
                LEFT JOIN LATERAL unnest(i.tool_run_ids) WITH ORDINALITY ref(run_id,position) ON true
                LEFT JOIN tool_runs r ON r.tool_run_id=ref.run_id
                LEFT JOIN tool_definitions d ON d.tool_id=r.tool_id
                LEFT JOIN movement_analyses m ON m.analysis_id=r.movement_analysis_id
                LEFT JOIN outlook_analyses o ON o.analysis_id=r.outlook_analysis_id
                WHERE a.analysis_id IN (:ids)
                ORDER BY a.analysis_id,s.position,ref.position
                """, ids)) {
            String id=text(row,"analysis_id");
            JsonNode a=analyses.get(id), run=row.path("run"), def=row.path("definition");
            require(validOwner(row.path("owner"), a) && validOwner(row.path("run_owner"), a)
                    && text(row.path("owner"),"trading_date").equals(text(a,"trading_date"))
                    && "completed".equals(text(run,"status")), "Invalid selected evidence reference");
            String item=text(row,"item_id"), runId=text(run,"tool_run_id");
            foundItems.add(id+":"+item);
            JsonNode output=run.path("output"), args=run.path("arguments");
            require(runId.equals(text(output,"tool_run_id")) && output.path("result").isObject(),
                    "Stored tool result is incomplete");
            String key=id+":"+runId;
            evidenceRows.putIfAbsent(key,row);
            itemsByRun.computeIfAbsent(key,k->new ArrayList<>()).add(item);
        }
        for (var stored : evidenceRows.entrySet()) {
            JsonNode row=stored.getValue(), run=row.path("run"), def=row.path("definition");
            String id=text(row,"analysis_id"),runId=text(run,"tool_run_id");
            JsonNode args=run.path("arguments"),output=run.path("output");
            List<String> itemIds=itemsByRun.get(stored.getKey());
            List<BundleEvidence> list=evidence.computeIfAbsent(id,k->new ArrayList<>());
            if ("get_issue_evidence".equals(text(def,"function_name"))) {
                require(args.path("include_body").isBoolean() && !args.path("include_body").asBoolean(),
                        "Exploration news cannot be final evidence");
                require(output.path("result").path("news").size()>0, "Empty news evidence");
                for (JsonNode article : output.path("result").path("news")) {
                    String newsId=text(article,"news_id");
                    JsonNode doc=row.path("news").path(newsId);
                    require(doc.isObject(), "Missing news metadata");
                    list.add(new EvidenceItem(text(doc,"document_type"),text(article,"title"),
                            text(doc,"source_code"),nullable(doc,"published_at"),nullable(doc,"source_uri"),
                            newsId,runId,itemIds));
                }
            } else {
                List<String> sources=new ArrayList<>();
                def.path("source_names").forEach(v->sources.add(v.asString()));
                list.add(new CalculationEvidenceItem(text(def,"description"),String.join(" · ",sources),
                        null,runId,itemIds,args,output,nullable(def,"formula_latex"),text(def,"description")));
            }
        }
        Map<String, Content> result=new HashMap<>();
        for (var entry : analyses.entrySet()) {
            String id=entry.getKey(); JsonNode a=entry.getValue();
            for (JsonNode item : a.path("selected_item_ids"))
                require(foundItems.contains(id+":"+item.asString()), "Selected item has no evidence");
            Instant at=Instant.parse(text(a,"analysis_at").replace(" ","T"));
            result.put(id,new Content(new ExplanationResult(id,text(a,"instrument_id"),text(a,"etf_code"),
                    text(a,"name"),LocalDate.parse(text(a,"trading_date")),at,null,text(a,"summary"),
                    null,null,at,"v2"),evidence.get(id)));
        }
        return result;
    }

    private boolean validOwner(JsonNode owner, JsonNode analysis) {
        return owner.isObject() && "completed".equals(text(owner,"status"))
                && "database".equals(text(owner,"data_source"))
                && text(owner,"etf_code").equals(text(analysis,"etf_code"))
                && !Instant.parse(text(owner,"analysis_at")).isAfter(Instant.parse(text(analysis,"analysis_at")));
    }

    @SuppressWarnings("unchecked")
    private List<JsonNode> query(String sql, Set<String> ids) {
        List<String> rows=entityManager.createNativeQuery(sql,String.class).setParameter("ids",ids).getResultList();
        return rows.stream().map(json::readTree).toList();
    }
    private static String text(JsonNode node, String key) {
        String value=nullable(node,key);
        require(value!=null, "Missing evidence field: "+key);
        return value;
    }
    private static String nullable(JsonNode node, String key) { return node.path(key).asString(null); }
    private static void require(boolean ok,String message) { if (!ok) throw new IllegalStateException(message); }
}
