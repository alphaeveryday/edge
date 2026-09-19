package com.edge.app.repository;

import com.edge.app.entity.VoteChoice;
import lombok.RequiredArgsConstructor;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

@Repository
@ConditionalOnProperty(name = "vote.mode", havingValue = "write-behind")
@RequiredArgsConstructor
public class VoteFlushRepository {
    private final JdbcTemplate jdbcTemplate;

    public void upsertAll(Long forecastId, Map<Long, VoteChoice> votes) {
        if (votes.isEmpty()) {
            return;
        }
        String rows = String.join(", ", Collections.nCopies(votes.size(), "(?, ?, ?)"));
        List<Object> args = new ArrayList<>();
        votes.forEach((userId, choice) -> {
            args.add(forecastId);
            args.add(userId);
            args.add(choice.name());
        });
        jdbcTemplate.update("insert into forecast_vote(forecast_id, user_id, choice) values " + rows
                + " as new on duplicate key update choice = new.choice", args.toArray());
    }
}
