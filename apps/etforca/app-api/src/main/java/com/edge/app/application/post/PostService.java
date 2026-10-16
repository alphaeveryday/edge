package com.edge.app.application.post;

import com.edge.app.application.etf.required.EtfRepository;
import com.edge.app.application.member.provided.AppPrincipal;
import com.edge.app.application.member.required.MemberRepository;
import com.edge.app.application.member.required.PrincipalRepository;
import com.edge.app.application.post.provided.CommentCreateRequest;
import com.edge.app.application.post.provided.CommentResponse;
import com.edge.app.application.post.provided.PostCreateRequest;
import com.edge.app.application.post.provided.PostResponse;
import com.edge.app.application.post.required.CommentRepository;
import com.edge.app.application.post.required.PostLikeRepository;
import com.edge.app.application.post.required.PostRepository;
import com.edge.app.application.post.required.PostTagRepository;
import com.edge.app.application.watch.required.WatchItemRepository;
import com.edge.app.domain.etf.Etf;
import com.edge.app.domain.member.Member;
import com.edge.app.domain.post.Comment;
import com.edge.app.domain.post.CommentCreated;
import com.edge.app.domain.post.CommentDeleted;
import com.edge.app.domain.post.Post;
import com.edge.app.domain.post.PostScope;
import com.edge.app.domain.post.PostTag;
import com.edge.app.support.cursor.Cursor;
import com.edge.app.support.cursor.PageResponse;
import com.edge.app.support.exception.AppErrorStatus;
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

/**
 * 커뮤니티 정책
 * 회원과 관심 ETF 한정의 쓰기
 * 글당 태그 3개 상한
 * 소프트 삭제
 */
@Service
@RequiredArgsConstructor
public class PostService {
    private static final int TAG_MAX = 3;
    private static final String DELETED_MEMBER_NAME = "탈퇴한 사용자";
    // 피드는 상한, 댓글은 하한인 첫 페이지 sentinel
    private static final Cursor FEED_START = new Cursor(Instant.parse("9999-12-31T00:00:00Z"), Long.MAX_VALUE);
    private static final Cursor COMMENT_START = new Cursor(Instant.EPOCH, 0);
    // 차단 행이 없는 0 으로 두는 게스트의 차단 필터 조회자
    private static final long NO_VIEWER = 0;

    private final PostRepository postRepository;
    private final PostTagRepository tagRepository;
    private final CommentRepository commentRepository;
    private final PostLikeRepository likeRepository;
    private final PrincipalRepository principalRepository;
    private final WatchItemRepository watchItemRepository;
    private final EtfRepository etfRepository;
    private final MemberRepository memberRepository;
    private final ApplicationEventPublisher eventPublisher;

    @Transactional
    public PageResponse<PostResponse> feed(AppPrincipal principal, PostScope scope, String code, Cursor cursor, int size) {
        Long requester = principal.memberId();
        long viewer = requester == null ? NO_VIEWER : requester;
        if (scope == PostScope.HOT) {
            return new PageResponse<>(responses(postRepository.hot(viewer, Limit.of(size)), requester), null);
        }
        boolean byCodes = false;
        List<String> codes = List.of("");
        if (scope == PostScope.MINE) {
            codes = watchItemRepository.watchedCodes(principalRepository.resolve(principal));
            if (codes.isEmpty()) {
                return new PageResponse<>(List.of(), null);
            }
            byCodes = true;
        }
        Cursor from = cursor == null ? FEED_START : cursor;
        List<Post> posts = postRepository.feed(from.createdAt(), from.id(), code != null, code == null ? "" : code,
                byCodes, codes, viewer, Limit.of(size + 1));
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
    // 차단한 작성자 글의 blocked 표시
    @Transactional
    public PostResponse get(String id, AppPrincipal principal) {
        Post post = existing(id);
        postRepository.addView(post.getId());
        Long requester = principal == null ? null : principal.memberId();
        PostResponse response = responses(List.of(postRepository.findById(post.getId()).orElseThrow()), requester).get(0);
        return requester != null && postRepository.blocked(requester, post.getAuthorId()) ? response.asBlocked() : response;
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
    public PageResponse<CommentResponse> comments(String id, AppPrincipal principal, Cursor cursor, int size) {
        Post post = existing(id);
        Cursor from = cursor == null ? COMMENT_START : cursor;
        long viewer = principal == null || principal.memberId() == null ? NO_VIEWER : principal.memberId();
        List<Comment> comments = commentRepository.page(post.getId(), from.createdAt(), from.id(), viewer, Limit.of(size + 1));
        return page(comments, size, r -> new Cursor(r.getCreatedAt(), r.getId()), this::commentResponses);
    }

    @Transactional
    public CommentResponse comment(long memberId, String id, CommentCreateRequest request) {
        Post post = existing(id);
        Comment comment = commentRepository.save(Comment.create(post.getId(), memberId, request.body().trim(), Instant.now()));
        postRepository.addComment(post.getId());
        eventPublisher.publishEvent(new CommentCreated(post.getId(), comment.getId(), post.getAuthorId(), memberId, comment.getBody()));
        return commentResponses(List.of(comment)).get(0);
    }

    // 자리 표시 없는 삭제와 댓글 수 감소
    // 그 댓글로 생긴 알림의 삭제
    @Transactional
    public void removeComment(long memberId, String id, String commentId) {
        Post post = existing(id);
        Comment comment = existingComment(post.getId(), commentId);
        if (comment.getAuthorId() != memberId) {
            throw new GeneralException(ErrorStatus._FORBIDDEN);
        }
        if (commentRepository.softDelete(comment.getId(), Instant.now()) > 0) {
            postRepository.subtractComment(post.getId());
            eventPublisher.publishEvent(new CommentDeleted(comment.getId()));
        }
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

    private Comment existingComment(long postId, String commentId) {
        try {
            return commentRepository.findByIdAndPostIdAndDeletedAtIsNull(Long.parseLong(commentId), postId)
                    .orElseThrow(() -> new GeneralException(AppErrorStatus.COMMENT_NOT_FOUND));
        } catch (NumberFormatException e) {
            throw new GeneralException(AppErrorStatus.COMMENT_NOT_FOUND);
        }
    }

    // size+1 조회 기반의 다음 페이지 판정
    // 마지막 행 기준의 커서 생성
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
                    authors.get(p.getAuthorId()), p.getCreatedAt(), p.getTitle(), p.getBody(), p.getQuoteTag(),
                    p.getLikeCount(), p.getCommentCount(), liked.contains(p.getId()), p.getViewCount(),
                    requester != null && requester.equals(p.getAuthorId()), null);
        }).toList();
    }

    private List<CommentResponse> commentResponses(List<Comment> comments) {
        Map<Long, PostResponse.Author> authors = authors(comments.stream().map(Comment::getAuthorId).collect(Collectors.toSet()));
        return comments.stream().map(r -> new CommentResponse(Long.toString(r.getId()), authors.get(r.getAuthorId()),
                r.getCreatedAt(), r.getBody())).toList();
    }

    // 탈퇴 회원 이름 가림
    private Map<Long, PostResponse.Author> authors(Set<Long> memberIds) {
        return memberRepository.findAllById(memberIds).stream().collect(Collectors.toMap(Member::getId,
                m -> new PostResponse.Author(m.getDeletedAt() == null ? m.getNick() : DELETED_MEMBER_NAME, m.getHandle())));
    }
}
