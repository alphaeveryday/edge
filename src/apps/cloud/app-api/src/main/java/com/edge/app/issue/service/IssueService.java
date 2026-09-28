package com.edge.app.issue.service;

import com.edge.app.analysis.repository.EtfAnalysisRepository;
import com.edge.app.common.AppErrorStatus;
import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.common.cursor.Cursor;
import com.edge.app.common.cursor.PageResponse;
import com.edge.app.common.json.Payload;
import com.edge.app.etf.dto.EtfSummaryResponse;
import com.edge.app.etf.entity.Dir;
import com.edge.app.etf.entity.Etf;
import com.edge.app.etf.entity.Signal;
import com.edge.app.etf.repository.EtfRepository;
import com.edge.app.issue.dto.IssueDetailResponse;
import com.edge.app.issue.dto.IssueRowResponse;
import com.edge.app.issue.entity.Issue;
import com.edge.app.issue.entity.IssueTab;
import com.edge.app.issue.repository.IssueRepository;
import com.edge.app.member.repository.PrincipalRepository;
import com.edge.app.watch.repository.WatchItemRepository;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.function.Function;
import java.util.stream.Collectors;

/** 커서는 (as_of, rank) 를 Cursor(createdAt=as_of 자정 UTC, id=rank) 에 싣는다. */
@Service
@RequiredArgsConstructor
public class IssueService {
    private static final Cursor START = new Cursor(Instant.parse("9999-12-31T00:00:00Z"), 0);

    private final IssueRepository issueRepository;
    private final EtfRepository etfRepository;
    private final EtfAnalysisRepository analysisRepository;
    private final PrincipalRepository principalRepository;
    private final WatchItemRepository watchItemRepository;

    // principal 해소가 upsert 라 readOnly 가 아니다.
    @Transactional
    public PageResponse<IssueRowResponse> list(AppPrincipal principal, IssueTab tab, Cursor cursor, int size) {
        List<String> codes = List.of();
        if (tab == IssueTab.MINE) {
            codes = watchItemRepository.watchedCodes(principalRepository.resolve(principal));
            if (codes.isEmpty()) {
                return new PageResponse<>(List.of(), null);
            }
        }
        Cursor from = cursor == null ? START : cursor;
        List<Issue> rows = issueRepository.page(LocalDate.ofInstant(from.createdAt(), ZoneOffset.UTC), (int) from.id(),
                tab == IssueTab.MINE, codes, size + 1);
        boolean more = rows.size() > size;
        List<Issue> visible = more ? rows.subList(0, size) : rows;
        Issue last = visible.isEmpty() ? null : visible.get(visible.size() - 1);
        String next = more ? new Cursor(last.getAsOf().atStartOfDay(ZoneOffset.UTC).toInstant(), last.getRank()).encode() : null;
        return new PageResponse<>(responses(visible), next);
    }

    @Transactional(readOnly = true)
    public IssueDetailResponse get(String id) {
        Issue issue = issueRepository.findById(id).orElseThrow(() -> new GeneralException(AppErrorStatus.ISSUE_NOT_FOUND));
        Payload p = Payload.parse(issue.getPayload());
        Payload effect = p.get("effect");
        List<String> codes = p.list("affected").stream().map(a -> a.text("code")).filter(c -> !c.isEmpty()).toList();
        return new IssueDetailResponse(issue.getId(), issue.getTitle(), p.text("body"), p.texts("points"),
                p.list("sources").stream().map(s -> new IssueDetailResponse.Source(s.text("title"), s.text("pub"), s.text("url"))).toList(),
                new IssueDetailResponse.Effect(effect.text("theme"), Dir.fold(effect.text("dir")), effect.text("body")),
                affected(codes));
    }

    private List<IssueRowResponse> responses(List<Issue> issues) {
        List<String> codes = issues.stream().map(Issue::getEtfCode).filter(Objects::nonNull).distinct().toList();
        Map<String, Etf> etfs = codes.isEmpty() ? Map.of()
                : etfRepository.findAllById(codes).stream().collect(Collectors.toMap(Etf::getCode, Function.identity()));
        return issues.stream().map(i -> {
            Etf etf = i.getEtfCode() == null ? null : etfs.get(i.getEtfCode());
            return new IssueRowResponse(i.getId(), i.getRank(), i.getDelta(), i.getTitle(), i.getKw(),
                    etf == null ? null : new IssueRowResponse.Etf(etf.getCode(), etf.getName(), etf.getThemeKey(), etf.getSub()));
        }).toList();
    }

    /** affected = EtfSummary + prev(직전 발행본 signal). 동기화에서 빠진 코드는 숨긴다. */
    private List<IssueDetailResponse.Affected> affected(List<String> codes) {
        if (codes.isEmpty()) {
            return List.of();
        }
        Map<String, EtfSummaryResponse> byCode = etfRepository.summaries(codes).stream()
                .map(EtfSummaryResponse::from).collect(Collectors.toMap(EtfSummaryResponse::code, Function.identity()));
        return codes.stream().map(byCode::get).filter(Objects::nonNull).map(e -> {
            Signal prev = analysisRepository.findTopByEtfCodeOrderByAsOfDesc(e.code())
                    .flatMap(a -> analysisRepository.findTopByEtfCodeAndAsOfLessThanOrderByAsOfDesc(e.code(), a.getAsOf()))
                    .map(a -> Signal.of(a.getSignal())).orElse(null);
            return new IssueDetailResponse.Affected(e.code(), e.name(), e.theme(), e.price(), e.changePct(), e.signal(),
                    e.hot(), e.sub(), prev);
        }).toList();
    }
}
