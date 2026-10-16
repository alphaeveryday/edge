package com.edge.app.application.report;

import com.edge.app.adapter.integration.mail.Mailer;
import com.edge.app.application.mail.MailQuota;
import com.edge.app.application.member.required.MemberRepository;
import com.edge.app.application.post.required.CommentRepository;
import com.edge.app.application.post.required.PostRepository;
import com.edge.app.application.report.provided.ReportRequest;
import com.edge.app.application.report.required.ReportRepository;
import com.edge.app.domain.member.Member;
import com.edge.app.support.exception.AppErrorStatus;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 신고 저장과 운영자 메일
 * 같은 대상 재신고의 저장·메일 없는 성공
 */
@Service
@RequiredArgsConstructor
public class ReportService {
    private final ReportRepository reportRepository;
    private final PostRepository postRepository;
    private final CommentRepository commentRepository;
    private final MemberRepository memberRepository;
    private final Mailer mailer;
    private final MailQuota mailQuota;

    @Value("${app.mail.operator}")
    private String operator;

    private record Target(long authorId, String body) {
    }

    @Transactional
    public void report(long memberId, ReportRequest request) {
        long targetId = parseId(request.targetId());
        Target target = "post".equals(request.targetType())
                ? postRepository.findByIdAndDeletedAtIsNull(targetId).map(p -> new Target(p.getAuthorId(), p.getBody())).orElse(null)
                : commentRepository.findById(targetId).filter(r -> r.getDeletedAt() == null)
                        .map(r -> new Target(r.getAuthorId(), r.getBody())).orElse(null);
        if (target == null) {
            throw new GeneralException(AppErrorStatus.POST_NOT_FOUND);
        }
        if (reportRepository.insertIfAbsent(memberId, request.targetType(), targetId, request.reason()) == 0) {
            return;
        }
        // 상한과 무관한 운영자 메일 발송
        // 운영자 메일의 발송 수 반영
        mailQuota.count();
        mailer.send(operator, "[ETF Orca 신고] " + request.reason() + " " + request.targetType() + " " + targetId,
                "사유: " + request.reason() + "\n대상: " + request.targetType() + " " + targetId
                        + "\n작성자: " + handle(target.authorId()) + "\n신고자: " + handle(memberId)
                        + "\n\n원문:\n" + target.body()
                        + "\n\n처리: 24시간 안에 확인 후 해당 행의 deleted_at 을 채운다.");
    }

    private String handle(long memberId) {
        return memberRepository.findById(memberId).map(Member::getHandle).orElse("#" + memberId);
    }

    private static long parseId(String id) {
        try {
            return Long.parseLong(id);
        } catch (NumberFormatException e) {
            throw new GeneralException(AppErrorStatus.POST_NOT_FOUND);
        }
    }
}
