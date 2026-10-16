package com.edge.app.application.watch.required;

import com.edge.app.domain.watch.WatchGroup;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface WatchGroupRepository extends JpaRepository<WatchGroup, Long> {
    List<WatchGroup> findByPrincipalIdOrderByPosition(long principalId);

    Optional<WatchGroup> findByPrincipalIdAndKey(long principalId, String key);

    Optional<WatchGroup> findByPrincipalIdAndIsDefaultTrue(long principalId);

    long countByPrincipalIdAndIsDefaultFalse(long principalId);
}
