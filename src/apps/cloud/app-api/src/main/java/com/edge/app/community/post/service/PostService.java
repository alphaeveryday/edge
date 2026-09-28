package com.edge.app.community.post.service;

import com.edge.app.common.AppErrorStatus;
import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.common.cursor.Cursor;
import com.edge.app.common.cursor.PageResponse;
import com.edge.app.community.post.dto.PostCreateRequest;
import com.edge.app.community.post.dto.PostResponse;
import com.edge.app.community.post.dto.ReplyCreateRequest;
import com.edge.app.community.post.dto.ReplyResponse;
import com.edge.app.community.post.entity.Post;
import com.edge.app.community.post.entity.PostScope;
import com.edge.app.community.post.entity.PostTag;
import com.edge.app.community.post.entity.Reply;
import com.edge.app.community.post.event.ReplyCreated;
import com.edge.app.community.post.repository.PostLikeRepository;
import com.edge.app.community.post.repository.PostRepository;
import com.edge.app.community.post.repository.PostTagRepository;
import com.edge.app.community.post.repository.ReplyRepository;
import com.edge.app.etf.entity.Etf;
import com.edge.app.etf.repository.EtfRepository;
import com.edge.app.member.entity.Member;
import com.edge.app.member.repository.MemberRepository;
import com.edge.app.member.repository.PrincipalRepository;
import com.edge.app.watch.repository.WatchItemRepository;
import com.edge.common.apipayload.code.status.ErrorStatus;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.data.domain.Limit;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Function;
import java.util.stream.Collectors;

/** 커뮤니티 정책. 쓰기는 회원, 관심 ETF 한정, 태그 3개 상한, 소프트 삭제 */
@Service
@RequiredArgsConstructor
public class PostService {
    private static final int TAG_MAX = 3;
    private static final String DELETED_MEMBER_NAME = "탈퇴한 사용자";
    // 첫 페이지 sentinel. 피드는 상한, 답글은 하한
    private static final Cursor FEED_START = new Cursor(Instant.parse("9999-12-31T00:00:00Z"), Long.MAX_VALUE);
    private static final Cursor REPLY_START = new Cursor(Instant.EPOCH, 0);

    private final PostRepository postRepository;
    private final PostTagRepository tagRepository;
    private final ReplyRepository replyRepository;
    private final PostLikeRepository likeRepository;
    private final PrincipalRepository principalRepository;
    private final WatchItemRepository watchItemRepository;
    private final EtfRepository etfRepository;
    private final MemberRepository memberRepository;
    private final ApplicationEventPublisher eventPublisher;

    @Transactional
    public PageResponse<PostResponse> feed(AppPrincipal principal, PostScope scope, String code, Cursor cursor, int size) {
        Long requester = principal.memberId();
        if (scope == PostScope.HOT) {
            return new PageResponse<>(responses(postRepository.hot(Limit.of(size)), requester), null);
        }
        boolean byCodes = false;
        List<String> codes = List.of("");
        if (scope == PostScope.MINE) {
            if (!principal.isMember()) {
                throw new GeneralException(ErrorStatus._UNAUTHORIZED);
            }
            codes = watchItemRepository.watchedCodes(principalRepository.upsertMember(requester));
            if (codes.isEmpty()) {
                return new PageResponse<>(List.of(), null);
            }
            byCodes = true;
        }
        Cursor from = cursor == null ? FEED_START : cursor;
        List<Post> posts = postRepository.feed(from.createdAt(), from.id(), code != null, code == null ? "" : code,
                byCodes, codes, Limit.of(size + 1));
        return page(posts, size, p -> new Cursor(p.getCreatedAt(), p.getId()), list -> responses(list, requester));
    }

    @Transactional
    public PostResponse create(long memberId, PostCreateRequest request) {
        List<String> tags = new ArrayList<>(new LinkedHashSet<>(request.tags()));
        if (tags.isEmpty()) {
            throw new GeneralException(ErrorStatus._BAD_REQUEST);
        }
        if (tags.size() > TAG_MAX) {
            throw new GeneralException(AppErrorStatus.POST_TOO_MANY_TAGS);
        }
        if (etfRepository.countByCodeIn(tags) != tags.size()) {
            throw new GeneralException(AppErrorStatus.ETF_NOT_FOUND);
        }
        long principalId = principalRepository.upsertMember(memberId);
        if (watchItemRepository.watchedAmong(principalId, tags).size() != tags.size()) {
            throw new GeneralException(AppErrorStatus.POST_NOT_WATCHED_ETF);
        }
        Post post = postRepository.save(Post.create(memberId, tags.get(0), request.body().trim(), Instant.now()));
        for (int i = 0; i < tags.size(); i++) {
            tagRepository.save(PostTag.of(post.getId(), tags.get(i), i));
        }
        return responses(List.of(post), memberId).get(0);
    }

    // 조회수 증가와 요청자 기준 liked·mine 채움
    @Transactional
    public PostResponse get(String id, AppPrincipal principal) {
        Post post = existing(id);
        postRepository.addView(post.getId());
        return responses(List.of(postRepository.findById(post.getId()).orElseThrow()),
                principal == null ? null : principal.memberId()).get(0);
    }

    @Transactional
    public void remove(long memberId, String id) {
        Post post = existing(id);
        if (post.getAuthorId() != memberId) {
            throw new GeneralException(ErrorStatus._FORBIDDEN);
        }
        post.delete(Instant.now());
        tagRepository.deleteByPostId(post.getId());
        likeRepository.deleteByPostId(post.getId());
    }

    @Transactional(readOnly = true)
    public PageResponse<ReplyResponse> replies(String id, Cursor cursor, int size) {
        Post post = existing(id);
        Cursor from = cursor == null ? REPLY_START : cursor;
        List<Reply> replies = replyRepository.page(post.getId(), from.createdAt(), from.id(), Limit.of(size + 1));
        return page(replies, size, r -> new Cursor(r.getCreatedAt(), r.getId()), this::replyResponses);
    }

    @Transactional
    public ReplyResponse reply(long memberId, String id, ReplyCreateRequest request) {
        Post post = existing(id);
        Reply reply = replyRepository.save(Reply.create(post.getId(), memberId, request.body().trim(), Instant.now()));
        postRepository.addReply(post.getId());
        eventPublisher.publishEvent(new ReplyCreated(post.getId(), post.getAuthorId(), memberId, reply.getBody()));
        return replyResponses(List.of(reply)).get(0);
    }

    @Transactional
    public PostResponse like(long memberId, String id) {
        Post post = existing(id);
        int inserted = likeRepository.insertIfAbsent(post.getId(), memberId);
        if (inserted > 0) {
            postRepository.addLikes(post.getId(), 1);
        }
        return responses(List.of(postRepository.findById(post.getId()).orElseThrow()), memberId).get(0);
    }

    @Transactional
    public PostResponse unlike(long memberId, String id) {
        Post post = existing(id);
        if (likeRepository.deleteIfPresent(post.getId(), memberId) > 0) {
            postRepository.addLikes(post.getId(), -1);
        }
        return responses(List.of(postRepository.findById(post.getId()).orElseThrow()), memberId).get(0);
    }

    private Post existing(String id) {
        try {
            return postRepository.findByIdAndDeletedAtIsNull(Long.parseLong(id))
                    .orElseThrow(() -> new GeneralException(AppErrorStatus.POST_NOT_FOUND));
        } catch (NumberFormatException e) {
            throw new GeneralException(AppErrorStatus.POST_NOT_FOUND);
        }
    }

    // size+1 조회로 다음 페이지 판정, 마지막 행으로 커서 생성
    private static <E, R> PageResponse<R> page(List<E> rows, int size, Function<E, Cursor> cursorOf,
            Function<List<E>, List<R>> toResponses) {
        boolean more = rows.size() > size;
        List<E> visible = more ? rows.subList(0, size) : rows;
        String next = more ? cursorOf.apply(visible.get(visible.size() - 1)).encode() : null;
        return new PageResponse<>(toResponses.apply(visible), next);
    }

    private List<PostResponse> responses(List<Post> posts, Long requester) {
        if (posts.isEmpty()) {
            return List.of();
        }
        Map<String, Etf> etfs = etfRepository.findAllById(posts.stream().map(Post::getEtfCode).collect(Collectors.toSet()))
                .stream().collect(Collectors.toMap(Etf::getCode, Function.identity()));
        Map<Long, PostResponse.Author> authors = authors(posts.stream().map(Post::getAuthorId).collect(Collectors.toSet()));
        Set<Long> liked = requester == null ? Set.of()
                : new HashSet<>(likeRepository.likedAmong(requester, posts.stream().map(Post::getId).toList()));
        return posts.stream().map(p -> {
            Etf etf = etfs.get(p.getEtfCode());
            return new PostResponse(Long.toString(p.getId()),
                    new PostResponse.Etf(p.getEtfCode(), etf == null ? "" : etf.getThemeKey(), etf == null ? p.getEtfCode() : etf.getName()),
                    authors.get(p.getAuthorId()), p.getCreatedAt(), p.getTitle(), p.getBody(), p.getQuoteTag(), null,
                    p.getLikeCount(), p.getReplyCount(), p.getRepostCount(), liked.contains(p.getId()), p.getViewCount(),
                    requester != null && requester.equals(p.getAuthorId()));
        }).toList();
    }

    private List<ReplyResponse> replyResponses(List<Reply> replies) {
        Map<Long, PostResponse.Author> authors = authors(replies.stream().map(Reply::getAuthorId).collect(Collectors.toSet()));
        return replies.stream().map(r -> new ReplyResponse(Long.toString(r.getId()), authors.get(r.getAuthorId()),
                r.getCreatedAt(), r.getBody())).toList();
    }

    // 탈퇴 회원 이름 가림
    private Map<Long, PostResponse.Author> authors(Set<Long> memberIds) {
        return memberRepository.findAllById(memberIds).stream().collect(Collectors.toMap(Member::getId,
                m -> new PostResponse.Author(m.getDeletedAt() == null ? m.getNick() : DELETED_MEMBER_NAME, m.getHandle())));
    }
}
