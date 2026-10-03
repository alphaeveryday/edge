package com.edge.app.sync.service;

import com.edge.app.common.json.Payload;
import com.edge.app.etf.entity.Dir;
import com.edge.app.sync.repository.PipelineRepository;
import com.edge.app.sync.repository.PipelineRepository.Close;
import com.edge.app.sync.repository.PipelineRepository.Factor;
import com.edge.app.sync.repository.PipelineRepository.Holding;
import com.edge.app.sync.repository.PipelineRepository.Instrument;
import com.edge.app.sync.repository.PipelineRepository.Metric;
import com.edge.app.sync.repository.PipelineRepository.Movement;
import com.edge.app.sync.repository.PipelineRepository.MovementItem;
import com.edge.app.sync.repository.PipelineRepository.Outlook;
import com.edge.app.sync.repository.PipelineRepository.OutlookItem;
import com.edge.app.sync.repository.SyncRepository;
import com.edge.app.sync.repository.SyncRepository.Axis;
import com.edge.app.sync.repository.SyncRepository.Curation;
import com.edge.app.sync.repository.SyncRepository.Dated;
import com.edge.app.sync.repository.SyncRepository.Latest;
import com.edge.app.sync.repository.SyncRepository.Rank;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import net.javacrumbs.shedlock.spring.annotation.SchedulerLock;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;

/** 선별 ETF 의 파이프라인 RDS 데이터를 앱 테이블로 옮기는 동기화 */
@Slf4j
@Service
@ConditionalOnProperty("app.pipeline.url")
@RequiredArgsConstructor
public class SyncService {
    private static final ZoneId KST = ZoneId.of("Asia/Seoul");
    private static final LocalTime MARKET_CLOSE = LocalTime.of(15, 30);
    private static final Duration WINDOW = Duration.ofDays(30);
    private static final DateTimeFormatter DAY = DateTimeFormatter.ofPattern("M월 d일", Locale.KOREAN);
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final int HEAT_CELLS = 10;
    private static final List<String> FACTORS = List.of("이슈", "차트", "매크로", "밸류", "수급");
    private static final Map<String, String> AXIS = Map.of("이슈", "issue", "차트", "chart", "매크로", "macro", "밸류", "value", "수급", "flow");
    private static final List<String> SIGNALS = List.of("strongUp", "up", "neutral", "down", "strongDown");
    private static final Map<String, String> SIGNAL = Map.of("강력상승", "strongUp", "상승", "up", "중립", "neutral", "하락", "down", "강력하락", "strongDown");
    private static final BigDecimal EOK = new BigDecimal("100000000");
    /**
     * 엔진 대시보드 표기 기준의 지표 키별 라벨·단위
     * 표에 없는 키의 생략
     */
    private static final Map<String, MetricLabel> METRICS = Map.ofEntries(
            Map.entry("ma20_distance_pct", new MetricLabel("20일선 대비", "%", true)),
            Map.entry("ma60_direction", new MetricLabel("60일선 방향", "", false)),
            Map.entry("new_closing_high_count_20d", new MetricLabel("최근 20일 종가 신고가", "회", false)),
            Map.entry("distance_from_52w_closing_high_pct", new MetricLabel("52주 최고 종가 대비", "%", true)),
            Map.entry("turnover_ratio_previous_day", new MetricLabel("거래대금(전일)", "배", false)),
            Map.entry("atr14_pct", new MetricLabel("변동성(ATR)", "%", false)),
            Map.entry("usd_krw", new MetricLabel("원·달러 환율", "원", false)),
            Map.entry("commodity_return_20d_pct", new MetricLabel("원자재 20일 변화", "%", true)),
            Map.entry("kr_treasury_10y_yield", new MetricLabel("한국 국고채 10년", "%", false)),
            Map.entry("us_treasury_10y_yield", new MetricLabel("미국 국채 10년", "%", false)),
            Map.entry("brent_spot_usd", new MetricLabel("브렌트유 현물", "달러/배럴", false)),
            Map.entry("days_until_policy_decision", new MetricLabel("다음 금리 결정", "일 뒤", false)),
            Map.entry("weighted_per", new MetricLabel("구성종목 PER 가중평균", "배", false)),
            Map.entry("weighted_pbr", new MetricLabel("구성종목 PBR 가중평균", "배", false)),
            Map.entry("distribution_yield_12m_pct", new MetricLabel("최근 12개월 분배율", "%", false)),
            Map.entry("weighted_institution_net_amount_20d", new MetricLabel("기관 20일 가중 순매수", "억 원", true)),
            Map.entry("weighted_foreign_net_amount_20d", new MetricLabel("외국인 20일 가중 순매수", "억 원", true)),
            Map.entry("weighted_institution_net_buy_streak", new MetricLabel("기관 연속 순매수", "일", false)),
            Map.entry("weighted_foreign_net_buy_streak", new MetricLabel("외국인 연속 순매수", "일", false)),
            Map.entry("etf_units_change_20d_pct", new MetricLabel("발행좌수 20일 변화", "%", true)));
    private static final List<String> COUNT_UNITS = List.of("회", "일", "일 뒤");

    private final PipelineRepository pipeline;
    private final SyncRepository sync;
    private final TransactionTemplate tx;

    @Scheduled(fixedDelayString = "${app.sync.interval:PT10M}", initialDelayString = "${app.sync.initial-delay:PT30S}")
    @SchedulerLock(name = "pipeline-sync", lockAtMostFor = "PT9M")
    public void run() {
        List<Curation> curation = sync.curation();
        tx.executeWithoutResult(s -> sync.deleteOutside(curation.stream().map(Curation::code).toList()));
        int done = 0;
        for (Curation c : curation) {
            try {
                if (syncEtf(c)) {
                    done++;
                }
            } catch (RuntimeException e) {
                log.warn("sync failed code={}", c.code(), e);
            }
        }
        tx.executeWithoutResult(s -> rank());
        log.info("sync done etfs={}/{}", done, curation.size());
    }

    private boolean syncEtf(Curation c) {
        Instrument etf = pipeline.etf(c.code()).orElse(null);
        if (etf == null) {
            log.warn("sync skipped code={} reason=no-instrument", c.code());
            return false;
        }
        List<Close> closes = pipeline.closes(etf.instrumentId(), LocalDate.now(KST).minusYears(1));
        Close last = closes.isEmpty() ? null : closes.getLast();
        Close prev = closes.size() < 2 ? last : closes.get(closes.size() - 2);
        List<Holding> holdings = last == null ? List.of() : pipeline.holdings(etf.instrumentId(), prev.date(), last.date());
        Instant from = Instant.now().minus(WINDOW);
        List<Dated> moves = pipeline.movements(c.code(), from).stream().map(this::move).toList();
        List<Built> outlooks = pipeline.outlooks(c.code(), from).stream().map(this::outlook).filter(Objects::nonNull).toList();
        tx.executeWithoutResult(s -> {
            sync.upsertEtf(c.code(), etf.instrumentId(), etf.marketCode(), etf.name(), c);
            if (last != null) {
                sync.upsertQuote(c.code(), last.close(), pct(prev.close(), last.close()),
                        last.date().atTime(MARKET_CLOSE).atZone(KST).toInstant());
                sync.replaceCandles(c.code(), closes);
            }
            sync.upsertDetail(c.code(), holdings.isEmpty() ? LocalDate.now(KST) : holdings.getFirst().asOf(), detail(c, holdings));
            sync.replaceMoves(c.code(), moves);
            sync.deleteAnalysesExcept(c.code(), outlooks.stream().map(b -> b.analysis().asOf()).toList());
            for (Built b : outlooks) {
                sync.replaceAxes(sync.upsertAnalysis(c.code(), b.analysis(), b.signal()), b.axes());
            }
        });
        return true;
    }

    // 종목정보 구성종목의 비중 순 정렬
    // 종목정보 히트맵의 상위 종목 한정
    // 원천 없는 해석·테마·방향의 제외
    private String detail(Curation c, List<Holding> holdings) {
        List<Holding> sorted = holdings.stream().sorted(Comparator.comparingDouble(Holding::weightRatio).reversed()).toList();
        List<Map<String, Object>> stocks = sorted.stream().limit(HEAT_CELLS)
                .map(h -> map("name", stock(h), "weight", weight(h), "changePct", pct(h.prevClose(), h.lastClose()))).toList();
        List<Map<String, Object>> rows = sorted.stream().map(h -> map("name", stock(h), "weight", weight(h))).toList();
        List<Map<String, Object>> info = new ArrayList<>(List.of(
                map("k", "운용사", "v", c.manager()),
                map("k", "총보수", "v", "연 " + c.expenseRatio().stripTrailingZeros().toPlainString() + "%"),
                map("k", "상장일", "v", c.listedOn().toString())));
        if (c.leverage() != null) {
            info.add(map("k", "레버리지", "v", c.leverage().stripTrailingZeros().toPlainString() + "배"));
        }
        if (c.hedged() != null) {
            info.add(map("k", "환율", "v", c.hedged() ? "환헤지" : "환노출"));
        }
        return JSON.writeValueAsString(map("stocks", stocks, "holdings", rows, "stockCount", holdings.size(),
                "info", info, "blurb", c.blurb()));
    }

    // 선택된 항목이 있을 때 그 순서를 따르는 오늘 움직임
    private Dated move(Movement m) {
        List<MovementItem> all = pipeline.movementItems(m.id());
        List<MovementItem> items = m.selected().isEmpty() ? all
                : m.selected().stream().flatMap(id -> all.stream().filter(i -> i.id().equals(id))).toList();
        List<Map<String, Object>> rows = items.stream().map(i -> map("type", i.type(), "title_keyword", i.titleKeyword(),
                "sentence", i.sentence(), "sentiment", i.sentiment())).toList();
        return new Dated(m.tradingDate(), m.publishedAt(), JSON.writeValueAsString(map("summary", m.summary(), "items", rows)));
    }

    // 서버가 읽는 payload 키 기준의 전망 조립
    // 요인 페이지 축의 동시 생성
    private Built outlook(Outlook o) {
        String signal = SIGNAL.get(o.sticker());
        if (signal == null) {
            log.warn("sync skipped outlook={} reason=sticker:{}", o.id(), o.sticker());
            return null;
        }
        List<OutlookItem> items = pipeline.outlookItems(o.id());
        Map<String, Factor> factors = new LinkedHashMap<>();
        pipeline.factors(o.id()).stream().sorted(Comparator.comparingInt(f -> FACTORS.indexOf(f.type())))
                .forEach(f -> factors.put(f.type(), f));
        List<Map<String, Object>> detailItems = items.stream().filter(i -> i.section().equals("detail"))
                .map(i -> map("title_keyword", i.titleKeyword(), "sentences", JSON.readTree(i.bulletsJson()))).toList();
        List<String> updates = items.stream().filter(i -> i.section().equals("update"))
                .map(i -> i.sentence() == null ? i.titleKeyword() : i.sentence()).toList();
        List<Map<String, Object>> supports = new ArrayList<>();
        List<Map<String, Object>> burdens = new ArrayList<>();
        pipeline.keywords(o.id()).forEach(k -> (k.kind().equals("support") ? supports : burdens).add(map("label", k.label())));
        String payload = JSON.writeValueAsString(map(
                "summary_card", map("title", o.summaryTitle(), "summary", o.summary()),
                "detail", map("title", o.detailTitle(), "items", detailItems, "updates", map("items", updates)),
                "conclusion", map("title", o.conclusionTitle(), "sentence", o.conclusionSentence(),
                        "change_condition", o.changeCondition(), "supports", supports, "burdens", burdens),
                "factors", factors.values().stream()
                        .map(f -> map("axis", AXIS.get(f.type()), "sticker", f.sticker(), "summary", f.sentence())).toList()));
        return new Built(new Dated(o.asOf(), o.publishedAt(), payload), signal, axes(o, factors));
    }

    private List<Axis> axes(Outlook o, Map<String, Factor> factors) {
        List<Axis> axes = new ArrayList<>();
        List<Map<String, Object>> issues = pipeline.issueItems(o.id()).stream()
                .map(i -> map("title_keyword", i.titleKeyword(), "sentence", i.sentence(), "sentiment", i.sentiment())).toList();
        if (!issues.isEmpty()) {
            Factor f = factors.get("이슈");
            String headline = o.issueHeadline() != null ? o.issueHeadline() : f == null ? null : f.sentence();
            axes.add(axis("이슈", f, map("sticker", sticker(f), "headline", headline, "items", issues)));
        }
        Map<String, List<Map<String, Object>>> tiles = new LinkedHashMap<>();
        for (Metric m : pipeline.metrics(o.id())) {
            MetricLabel label = METRICS.get(m.key());
            if (label == null) {
                log.warn("sync metric skipped key={}", m.key());
                continue;
            }
            tiles.computeIfAbsent(m.factorType(), k -> new ArrayList<>()).add(map("label", label.label(),
                    "value", value(m, label), "unit", label.unit(), "subject", m.subject(), "observed_at", observed(m)));
        }
        tiles.forEach((type, metrics) -> {
            Factor f = factors.get(type);
            axes.add(axis(type, f, map("sticker", sticker(f), "headline", f == null ? null : f.sentence(), "metrics", metrics)));
        });
        return axes;
    }

    // 가장 최근 발행일 전망의 5단계 강한 순 탐색 순위
    // 같은 단계의 최근 발행 순 정렬
    private void rank() {
        List<Latest> latest = sync.latestAnalyses();
        LocalDate asOf = latest.stream().map(Latest::asOf).max(Comparator.naturalOrder()).orElse(LocalDate.now(KST));
        List<Latest> today = latest.stream().filter(l -> l.asOf().equals(asOf))
                .sorted(Comparator.comparingInt((Latest l) -> SIGNALS.indexOf(l.signal()))
                        .thenComparing(Latest::publishedAt, Comparator.reverseOrder()))
                .toList();
        List<Rank> ranks = new ArrayList<>();
        for (Latest l : today) {
            Payload p = Payload.parse(l.payload());
            List<Payload> chips = p.get("conclusion").list("supports");
            if (chips.isEmpty()) {
                chips = p.get("conclusion").list("burdens");
            }
            ranks.add(new Rank(ranks.size() + 1, l.code(), p.get("summary_card").text("title"),
                    JSON.writeValueAsString(chips.stream().limit(2).map(c -> c.text("label")).toList())));
        }
        sync.replaceRank(asOf, ranks);
    }

    private static Axis axis(String type, Factor f, Map<String, Object> payload) {
        return new Axis(AXIS.get(type), Dir.fold(sticker(f)).value(), JSON.writeValueAsString(payload));
    }

    private static String sticker(Factor f) {
        return f == null ? null : f.sticker();
    }

    private static String value(Metric m, MetricLabel label) {
        if (m.number() == null) {
            return m.text();
        }
        // 금액의 억 원 단위
        // 횟수·일수의 정수 표기
        // 나머지 지표의 소수 한 자리 표기
        BigDecimal n = label.unit().equals("억 원") ? m.number().divide(EOK, 8, RoundingMode.HALF_UP) : m.number();
        BigDecimal v = n.setScale(COUNT_UNITS.contains(label.unit()) ? 0 : 1, RoundingMode.HALF_UP);
        return (label.signed() && v.signum() > 0 ? "+" : "") + v.toPlainString();
    }

    private static String observed(Metric m) {
        LocalDate day = m.observedAt() != null ? m.observedAt().atZone(KST).toLocalDate() : m.observedDate();
        return day == null ? null : DAY.format(day);
    }

    private static BigDecimal pct(BigDecimal prev, BigDecimal last) {
        if (prev == null || last == null || prev.signum() == 0) {
            return BigDecimal.ZERO;
        }
        return last.divide(prev, 8, RoundingMode.HALF_UP).subtract(BigDecimal.ONE).movePointRight(2).setScale(2, RoundingMode.HALF_UP);
    }

    // 원천 표시명 끝의 보통주 표기 제거
    private static String stock(Holding h) {
        return h.name().replaceFirst(" 보통주$", "");
    }

    private static double weight(Holding h) {
        return BigDecimal.valueOf(h.weightRatio() * 100).setScale(1, RoundingMode.HALF_UP).doubleValue();
    }

    private static Map<String, Object> map(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) {
            m.put((String) kv[i], kv[i + 1]);
        }
        return m;
    }

    private record Built(Dated analysis, String signal, List<Axis> axes) {
    }

    private record MetricLabel(String label, String unit, boolean signed) {
    }
}
