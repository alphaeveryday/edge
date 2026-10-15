package com.edge.app.community.block.repository;

import com.edge.app.community.block.entity.MemberBlock;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface MemberBlockRepository extends JpaRepository<MemberBlock, MemberBlock.Key> {
    @Modifying
    @Query(value = "insert into member_block(blocker_id, blocked_id) values (:blocker, :blocked) on conflict do nothing",
            nativeQuery = true)
    void insertIfAbsent(@Param("blocker") long blockerId, @Param("blocked") long blockedId);

    boolean existsByBlockerIdAndBlockedId(long blockerId, long blockedId);
}
