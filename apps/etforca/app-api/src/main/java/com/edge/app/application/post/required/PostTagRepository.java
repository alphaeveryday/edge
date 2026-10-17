package com.edge.app.application.post.required;

import com.edge.app.domain.post.PostTag;
import org.springframework.data.jpa.repository.JpaRepository;

public interface PostTagRepository extends JpaRepository<PostTag, PostTag.Key> {
    void deleteByPostId(long postId);
}
