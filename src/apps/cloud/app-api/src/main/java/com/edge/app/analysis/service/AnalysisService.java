package com.edge.app.analysis.service;

import com.edge.app.analysis.dto.DailyAnalysisResponse;
import com.edge.app.analysis.dto.FactorPageResponse;
import com.edge.app.analysis.dto.MetricPageResponse;
import com.edge.app.analysis.entity.Axis;
import com.edge.app.analysis.entity.EtfAnalysis;
import com.edge.app.analysis.entity.EtfAnalysisAxis;
import com.edge.app.analysis.repository.EtfAnalysisAxisRepository;
import com.edge.app.analysis.repository.EtfAnalysisRepository;
import com.edge.app.common.AppErrorStatus;
import com.edge.app.common.json.Payload;
import com.edge.app.etf.entity.Dir;
import com.edge.app.etf.entity.Signal;
import com.edge.app.etf.repository.EtfRepository;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * 발행본 원문의 계약 매핑
 * erd.md 분석 절의 매핑 규칙
 * 앱 mock 과 같은 고정 문구
 */
@Service
@RequiredArgsConstructor
public class AnalysisService {
    private static final ZoneId KST = ZoneId.of("Asia/Seoul");
    private static final DateTimeFormatter DATELINE = DateTimeFormatter.ofPattern("'ETF Orca AI · 'M월 d일 EEEE HH:mm", Locale.KOREAN);
    private static final DateTimeFormatter DAY = DateTimeFormatter.ofPattern("M월 d일", Locale.KOREAN);
    private static final DateTimeFormatter WEEKDAY = DateTimeFormatter.ofPattern("E", Locale.KOREAN);

    private final EtfRepository etfRepository;
    private final EtfAnalysisRepository analysisRepository;
    private final EtfAnalysisAxisRepository axisRepository;

    @Transactional(readOnly = true)
    public DailyAnalysisResponse daily(String code, LocalDate date) {
        requireEtf(code);
        EtfAnalysis analysis = (date == null ? analysisRepository.findTopByEtfCodeOrderByAsOfDesc(code)
                : analysisRepository.findByEtfCodeAndAsOf(code, date))
                .orElseThrow(() -> new GeneralException(AppErrorStatus.ANALYSIS_NOT_READY));
        Payload p = Payload.parse(analysis.getPayload());
        Payload card = p.get("summary_card");
        Payload detail = p.get("detail");
        Payload conclusion = p.get("conclusion");
        Set<String> pages = Set.copyOf(axisRepository.axesOf(analysis.getId()));
        List<DailyAnalysisResponse.DailyDate> dates = analysisRepository.asOfs(code).stream()
                .map(d -> new DailyAnalysisResponse.DailyDate(d, WEEKDAY.format(d), String.valueOf(d.getDayOfMonth()), true))
                .toList();
        List<DailyAnalysisResponse.Arg> args = detail.list("items").stream()
                .map(item -> new DailyAnalysisResponse.Arg(String.valueOf(detail.list("items").indexOf(item) + 1),
                        item.text("title_keyword"), item.texts("sentences")))
                .toList();
        String close = conclusion.text("sentence");
        if (!conclusion.text("change_condition").isEmpty()) {
            close = close.isEmpty() ? conclusion.text("change_condition") : close + " " + conclusion.text("change_condition");
        }
        Signal prev = analysisRepository.findTopByEtfCodeAndAsOfLessThanOrderByAsOfDesc(code, analysis.getAsOf())
                .map(a -> Signal.of(a.getSignal())).orElse(null);
        return new DailyAnalysisResponse(analysis.getAsOf(), dates, "오늘 발행", card.text("title"),
                DATELINE.format(analysis.getPublishedAt().atZone(KST)), Signal.of(analysis.getSignal()), prev,
                card.text("summary"),
                p.list("factors").stream().map(f -> new DailyAnalysisResponse.AxisRead(Axis.of(f.text("axis")),
                        Dir.fold(f.text("sticker")), f.text("summary"), pages.contains(f.text("axis")))).toList(),
                detail.text("title"), DAY.format(analysis.getAsOf()), detail.get("updates").texts("items"), args,
                conclusion.text("title"), conclusion.list("burdens").stream().map(b -> b.text("label")).toList(),
                conclusion.list("supports").stream().map(s -> s.text("label")).toList(), close, null);
    }

    /** 이슈 축 payload 의 원문 매핑 */
    @Transactional(readOnly = true)
    public FactorPageResponse factor(String code, Axis axis) {
        EtfAnalysisAxis row = axisRow(code, axis);
        Payload p = Payload.parse(row.getPayload());
        return new FactorPageResponse(axis, Dir.fold(row.getDir()), p.text("headline"),
                p.list("items").stream().map(i -> new FactorPageResponse.Event(Dir.fold(i.text("sentiment")),
                        i.text("title_keyword"), i.text("sentence"))).toList(), null, null);
    }

    /** 수치 축 payload 의 원문 매핑 */
    @Transactional(readOnly = true)
    public MetricPageResponse metric(String code, Axis axis) {
        EtfAnalysisAxis row = axisRow(code, axis);
        Payload p = Payload.parse(row.getPayload());
        List<MetricPageResponse.Tile> tiles = p.list("metrics").stream().map(m -> new MetricPageResponse.Tile(
                m.text("label").isEmpty() ? m.text("key") : m.text("label"), m.text("value") + m.text("unit"),
                m.text("sticker").isEmpty() ? null : Dir.fold(m.text("sticker")), note(m), false)).toList();
        return new MetricPageResponse(axis, Dir.fold(row.getDir()), p.text("headline"), tiles, !p.list("items").isEmpty());
    }

    private static String note(Payload metric) {
        String subject = metric.text("subject");
        String observed = metric.text("observed_at");
        if (subject.isEmpty() && observed.isEmpty()) {
            return null;
        }
        return subject.isEmpty() || observed.isEmpty() ? subject + observed : subject + " · " + observed;
    }

    private EtfAnalysisAxis axisRow(String code, Axis axis) {
        requireEtf(code);
        return analysisRepository.findTopByEtfCodeOrderByAsOfDesc(code)
                .flatMap(a -> axisRepository.findById(new EtfAnalysisAxis.Key(a.getId(), axis.value())))
                .orElseThrow(() -> new GeneralException(AppErrorStatus.ANALYSIS_NOT_READY));
    }

    private void requireEtf(String code) {
        if (!etfRepository.existsById(code)) {
            throw new GeneralException(AppErrorStatus.ETF_NOT_FOUND);
        }
    }
}
