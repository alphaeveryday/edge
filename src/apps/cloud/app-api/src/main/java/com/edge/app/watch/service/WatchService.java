package com.edge.app.watch.service;

import com.edge.app.common.AppErrorStatus;
import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.etf.dto.EtfSummaryResponse;
import com.edge.app.etf.repository.EtfRepository;
import com.edge.app.member.repository.PrincipalRepository;
import com.edge.app.watch.dto.WatchGroupCreateRequest;
import com.edge.app.watch.dto.WatchGroupResponse;
import com.edge.app.watch.dto.WatchMembersRequest;
import com.edge.app.watch.dto.WatchMembershipRequest;
import com.edge.app.watch.entity.WatchGroup;
import com.edge.app.watch.entity.WatchItem;
import com.edge.app.watch.repository.WatchGroupRepository;
import com.edge.app.watch.repository.WatchItemRepository;
import com.edge.common.apipayload.code.status.ErrorStatus;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * 관심 그룹 관리
 * 첫 접근 시 기본 그룹 생성
 * 사용자 그룹 10개 상한
 * 기본 그룹 삭제 불가
 */
@Service
@RequiredArgsConstructor
public class WatchService {
    private static final int USER_GROUP_MAX = 10;
    private static final String KEY_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"; // gitleaks:allow
    private static final SecureRandom RANDOM = new SecureRandom();

    private final PrincipalRepository principalRepository;
    private final WatchGroupRepository groupRepository;
    private final WatchItemRepository itemRepository;
    private final EtfRepository etfRepository;

    @Transactional
    public List<WatchGroupResponse> groups(AppPrincipal principal) {
        long principalId = principalRepository.resolve(principal);
        base(principalId);
        return groupRepository.findByPrincipalIdOrderByPosition(principalId).stream()
                .map(g -> new WatchGroupResponse(g.getKey(), g.getLabel(), (int) itemRepository.countByGroupId(g.getId())))
                .toList();
    }

    @Transactional
    public WatchGroupResponse createGroup(AppPrincipal principal, WatchGroupCreateRequest request) {
        long principalId = principalRepository.resolve(principal);
        base(principalId);
        if (groupRepository.countByPrincipalIdAndIsDefaultFalse(principalId) >= USER_GROUP_MAX) {
            throw new GeneralException(AppErrorStatus.WATCH_TOO_MANY_GROUPS);
        }
        int position = groupRepository.findByPrincipalIdOrderByPosition(principalId).size();
        WatchGroup group = groupRepository.save(WatchGroup.user(principalId, newKey(), request.label().trim(), position));
        return new WatchGroupResponse(group.getKey(), group.getLabel(), 0);
    }

    // 삭제 그룹 종목의 기본 그룹 유지
    // 없는 그룹의 no-op 처리
    @Transactional
    public void deleteGroup(AppPrincipal principal, String key) {
        long principalId = principalRepository.resolve(principal);
        Optional<WatchGroup> found = groupRepository.findByPrincipalIdAndKey(principalId, key);
        if (found.isEmpty()) {
            return;
        }
        WatchGroup group = found.get();
        if (group.isDefault()) {
            throw new GeneralException(AppErrorStatus.WATCH_DEFAULT_GROUP_UNDELETABLE);
        }
        WatchGroup base = base(principalId);
        Set<String> inBase = codes(base.getId());
        int next = inBase.size();
        for (WatchItem item : itemRepository.findByGroupIdOrderByPosition(group.getId())) {
            if (inBase.add(item.getEtfCode())) {
                itemRepository.save(WatchItem.of(base.getId(), item.getEtfCode(), next++));
            }
        }
        itemRepository.deleteByGroupId(group.getId());
        groupRepository.delete(group);
    }

    @Transactional
    public List<EtfSummaryResponse> list(AppPrincipal principal, String key) {
        long principalId = principalRepository.resolve(principal);
        base(principalId);
        return groupRepository.findByPrincipalIdAndKey(principalId, key)
                .map(g -> summaries(itemRepository.findByGroupIdOrderByPosition(g.getId()).stream().map(WatchItem::getEtfCode).toList()))
                .orElse(List.of());
    }

    @Transactional
    public void setMembers(AppPrincipal principal, String key, WatchMembersRequest request) {
        long principalId = principalRepository.resolve(principal);
        base(principalId);
        WatchGroup group = groupRepository.findByPrincipalIdAndKey(principalId, key)
                .orElseThrow(() -> new GeneralException(ErrorStatus._BAD_REQUEST));
        List<String> codes = new ArrayList<>(new LinkedHashSet<>(request.codes()));
        requireEtfs(codes);
        itemRepository.deleteByGroupId(group.getId());
        itemRepository.flush();
        for (int i = 0; i < codes.size(); i++) {
            itemRepository.save(WatchItem.of(group.getId(), codes.get(i), i));
        }
    }

    // principal 해소 upsert 때문에 readOnly 미적용
    @Transactional
    public List<String> membership(AppPrincipal principal, String code) {
        return itemRepository.groupKeysOf(principalRepository.resolve(principal), code);
    }

    @Transactional
    public void setMembership(AppPrincipal principal, String code, WatchMembershipRequest request) {
        long principalId = principalRepository.resolve(principal);
        base(principalId);
        requireEtfs(List.of(code));
        Set<String> wanted = new HashSet<>(request.groups());
        for (WatchGroup group : groupRepository.findByPrincipalIdOrderByPosition(principalId)) {
            WatchItem.Key id = new WatchItem.Key(group.getId(), code);
            boolean has = itemRepository.existsById(id);
            if (wanted.contains(group.getKey()) && !has) {
                itemRepository.save(WatchItem.of(group.getId(), code, (int) itemRepository.countByGroupId(group.getId())));
            } else if (!wanted.contains(group.getKey()) && has) {
                itemRepository.deleteById(id);
            }
        }
    }

    /** 온보딩 완료 이벤트가 호출하는 멱등 기본 그룹 종목 추가 */
    @Transactional
    public void addToBase(long principalId, List<String> codes) {
        requireEtfs(codes);
        WatchGroup base = base(principalId);
        Set<String> inBase = codes(base.getId());
        int next = inBase.size();
        for (String code : codes) {
            if (inBase.add(code)) {
                itemRepository.save(WatchItem.of(base.getId(), code, next++));
            }
        }
    }

    private WatchGroup base(long principalId) {
        return groupRepository.findByPrincipalIdAndIsDefaultTrue(principalId)
                .orElseGet(() -> groupRepository.save(WatchGroup.base(principalId)));
    }

    private Set<String> codes(long groupId) {
        return itemRepository.findByGroupIdOrderByPosition(groupId).stream().map(WatchItem::getEtfCode)
                .collect(Collectors.toCollection(LinkedHashSet::new));
    }

    private List<EtfSummaryResponse> summaries(List<String> codes) {
        if (codes.isEmpty()) {
            return List.of();
        }
        Map<String, EtfSummaryResponse> byCode = etfRepository.summaries(codes).stream()
                .map(EtfSummaryResponse::from).collect(Collectors.toMap(EtfSummaryResponse::code, Function.identity()));
        // 동기화에 없는 코드 숨김
        // 관심 순서 유지
        return codes.stream().map(byCode::get).filter(Objects::nonNull).toList();
    }

    private void requireEtfs(List<String> codes) {
        if (!codes.isEmpty() && etfRepository.countByCodeIn(new HashSet<>(codes)) != new HashSet<>(codes).size()) {
            throw new GeneralException(AppErrorStatus.ETF_NOT_FOUND);
        }
    }

    private String newKey() {
        StringBuilder sb = new StringBuilder("g");
        for (int i = 0; i < 7; i++) {
            sb.append(KEY_ALPHABET.charAt(RANDOM.nextInt(KEY_ALPHABET.length())));
        }
        return sb.toString();
    }
}
