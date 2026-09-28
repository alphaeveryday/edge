package com.edge.app.community.vote.repository.writebehind;

import com.edge.app.community.vote.entity.VoteChoice;
import lombok.RequiredArgsConstructor;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

@Component
@ConditionalOnProperty(name = "vote.mode", havingValue = "write-behind")
@RequiredArgsConstructor
public class VoteFlushRepository {
    private final JdbcTemplate jdbcTemplate;

    public void upsertAll(String etfCode, Map<Long, VoteChoice> votes) {
        if (votes.isEmpty()) {
            return;
        }
        String rows = String.join(", ", Collections.nCopies(votes.size(), "(?, ?, ?)"));
        List<Object> args = new ArrayList<>();
        votes.forEach((memberId, choice) -> {
            args.add(etfCode);
            args.add(memberId);
            args.add(choice.value());
        });
        jdbcTemplate.update("insert into vote(etf_code, member_id, choice) values " + rows
                + " on conflict (etf_code, member_id) do update set choice = excluded.choice", args.toArray());
    }
}
