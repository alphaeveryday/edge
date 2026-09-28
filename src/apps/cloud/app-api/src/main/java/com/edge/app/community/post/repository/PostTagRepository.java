package com.edge.app.community.post.repository;

import com.edge.app.community.post.entity.PostTag;
import org.springframework.data.jpa.repository.JpaRepository;

public interface PostTagRepository extends JpaRepository<PostTag, PostTag.Key> {
    void deleteByPostId(long postId);
}
