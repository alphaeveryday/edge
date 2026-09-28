package com.edge.app.watch.repository;

import com.edge.app.watch.entity.WatchItem;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;

public interface WatchItemRepository extends JpaRepository<WatchItem, WatchItem.Key> {
    List<WatchItem> findByGroupIdOrderByPosition(long groupId);

    long countByGroupId(long groupId);

    void deleteByGroupId(long groupId);

    @Query("""
            select g.key from WatchGroup g join WatchItem i on i.groupId = g.id
            where g.principalId = :p and i.etfCode = :code order by g.position
            """)
    List<String> groupKeysOf(@Param("p") long principalId, @Param("code") String etfCode);

    /** principal 이 어느 그룹에든 담은 종목(관심 ETF). 커뮤니티 POST4002·scope=mine 이 읽는다. */
    @Query("""
            select distinct i.etfCode from WatchItem i join WatchGroup g on g.id = i.groupId
            where g.principalId = :p and i.etfCode in :codes
            """)
    List<String> watchedAmong(@Param("p") long principalId, @Param("codes") Collection<String> codes);

    @Query("select distinct i.etfCode from WatchItem i join WatchGroup g on g.id = i.groupId where g.principalId = :p")
    List<String> watchedCodes(@Param("p") long principalId);
}
