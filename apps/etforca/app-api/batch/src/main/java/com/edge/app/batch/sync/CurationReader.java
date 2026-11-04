package com.edge.app.batch.sync;

import com.edge.app.batch.sync.SyncRepository.Curation;
import lombok.RequiredArgsConstructor;
import org.springframework.batch.infrastructure.item.ExecutionContext;
import org.springframework.batch.infrastructure.item.ItemStreamReader;
import org.springframework.stereotype.Component;

import java.util.Iterator;

/**
 * step 시작 시 한 번 읽는 선별 목록
 * 조회 실패는 skip 대상인 read 가 아닌 open 의 step 실패
 */
@Component
@RequiredArgsConstructor
class CurationReader implements ItemStreamReader<Curation> {
    private final SyncRepository sync;
    private Iterator<Curation> items;

    @Override
    public void open(ExecutionContext context) {
        items = sync.curation().iterator();
    }

    @Override
    public Curation read() {
        return items.hasNext() ? items.next() : null;
    }
}
