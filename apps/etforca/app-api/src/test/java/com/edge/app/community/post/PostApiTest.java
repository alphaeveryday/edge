package com.edge.app.community.post;

import com.edge.app.member.MemberFixture;
import com.edge.app.support.stereotype.WebApiAdapterTest;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MockMvcTester.MockMvcRequestBuilder;
import org.springframework.test.web.servlet.assertj.MvcTestResult;
import tools.jackson.databind.ObjectMapper;

import java.util.List;
import java.util.Map;

import static com.edge.app.support.JsonResults.read;
import static com.edge.app.support.JsonResults.result;
import static org.assertj.core.api.Assertions.assertThat;

/** PRD 의 커뮤니티 정책과 커서 페이지 및 카운터 */
@WebApiAdapterTest
record PostApiTest(MockMvcTester mvcTester, ObjectMapper objectMapper, JdbcTemplate jdbc) {
    @BeforeAll
    static void seedEtfs(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key) values "
                + "('069500','i1','XKRX','KODEX 200','kospi'), ('133690','i2','XKRX','TIGER 나스닥100','us'), "
                + "('305720','i3','XKRX','KODEX 2차전지','battery'), ('091160','i4','XKRX','KODEX 반도체','semi') on conflict (code) do nothing");
    }

    @Test
    void writingRequiresWatchedEtfAndAtMostThreeTags() {
        String a = member("p1@example.com", "A", List.of("069500", "133690", "305720"));
        assertThat(call(HttpMethod.POST, "/api/v1/posts", Map.of("body", "b", "tags", List.of("091160")), a))
                .hasStatus(400)
                .bodyJson()
                .extractingPath("$.code").isEqualTo("POST4002");
        assertThat(call(HttpMethod.POST, "/api/v1/posts", Map.of("body", "b", "tags", List.of("069500", "133690", "305720", "091160")), a))
                .bodyJson()
                .extractingPath("$.code").isEqualTo("POST4003");
        assertThat(call(HttpMethod.POST, "/api/v1/posts", Map.of("body", "b", "tags", List.of("000000")), a))
                .bodyJson()
                .extractingPath("$.code").isEqualTo("ETF4001");
        assertThat(call(HttpMethod.POST, "/api/v1/posts", Map.of("body", "b", "tags", List.of()), a)).hasStatus(400);
        assertThat(call(HttpMethod.POST, "/api/v1/posts", Map.of("body", "b", "tags", List.of("069500")), "dev:g1")).hasStatus(401);

        Map<String, Object> created = post(a, "  첫 글 ", List.of("133690", "069500"));
        assertThat(created.get("body")).isEqualTo("첫 글");
        assertThat(created.get("mine")).isEqualTo(true);
        assertThat(created.get("liked")).isEqualTo(false);
        Map<String, Object> etf = (Map<String, Object>) created.get("etf");
        assertThat(etf.get("code")).as("대표 ETF 는 첫 태그").isEqualTo("133690");
        assertThat(etf.get("theme")).isEqualTo("us");
        assertThat(etf.get("short")).isEqualTo("TIGER 나스닥100");
        assertThat(((Map<?, ?>) created.get("author")).get("name")).isEqualTo("A");
        assertThat(created).doesNotContainKey("title");
        assertThat(created).as("리포스트 기능 없음").doesNotContainKey("repost");
    }

    @Test
    void feedScopesAndCursorPaging() {
        String a = member("p2@example.com", "A2", List.of("069500", "305720"));
        String b = member("p3@example.com", "B2", List.of("133690"));
        String p1 = (String) post(a, "one", List.of("069500")).get("id");
        String p2 = (String) post(a, "two", List.of("305720")).get("id");
        String p3 = (String) post(b, "three", List.of("133690")).get("id");

        Map<String, Object> page = result(call(HttpMethod.GET, "/api/v1/posts?size=2", null, "dev:g2"));
        assertThat(ids(page)).as("최신순").isEqualTo(List.of(p3, p2));
        assertThat(page.get("nextCursor")).isNotNull();
        Map<String, Object> next = result(call(HttpMethod.GET, "/api/v1/posts?size=2&cursor=" + page.get("nextCursor"), null, "dev:g2"));
        assertThat(ids(next)).isEqualTo(List.of(p1));
        assertThat(next.get("nextCursor")).isNull();

        Map<String, Object> byCode = result(call(HttpMethod.GET, "/api/v1/posts?code=305720", null, "dev:g2"));
        assertThat(ids(byCode)).isEqualTo(List.of(p2));

        // 회원 또는 게스트 디바이스인 요청자의 관심 ETF 글 목록
        Map<String, Object> mine = result(call(HttpMethod.GET, "/api/v1/posts?scope=mine", null, b));
        assertThat(ids(mine)).isEqualTo(List.of(p3));
        call(HttpMethod.PUT, "/api/v1/etfs/305720/watch-groups", Map.of("groups", List.of("base")), "dev:pf-mine");
        List<Object> guestMine = ids(result(call(HttpMethod.GET, "/api/v1/posts?scope=mine", null, "dev:pf-mine")));
        assertThat(guestMine.contains(p2) && !guestMine.contains(p1) && !guestMine.contains(p3)).as("게스트 관심 ETF 글만").isTrue();
        assertThat(items(result(call(HttpMethod.GET, "/api/v1/posts?scope=mine", null, "dev:pf-none")))).as("관심 없는 게스트는 빈 목록")
                .isEqualTo(List.of());
        assertThat(call(HttpMethod.GET, "/api/v1/posts?scope=random", null, "dev:g2")).hasStatus(400);
        assertThat(call(HttpMethod.GET, "/api/v1/posts?cursor=not-a-cursor", null, "dev:g2")).hasStatus(400);
    }

    @Test
    void likesAreIdempotentAndHotOrdersByLikes() {
        String a = member("p4@example.com", "A4", List.of("069500"));
        String b = member("p5@example.com", "B4", List.of("069500"));
        String cold = (String) post(a, "cold", List.of("069500")).get("id");
        String hot = (String) post(a, "hot", List.of("069500")).get("id");
        Map<String, Object> liked = result(call(HttpMethod.PUT, "/api/v1/posts/" + hot + "/like", null, b));
        assertThat(liked.get("like")).isEqualTo(1);
        assertThat(liked.get("liked")).isEqualTo(true);
        assertThat(call(HttpMethod.PUT, "/api/v1/posts/" + hot + "/like", null, b)).as("멱등").hasStatus2xxSuccessful().bodyJson().extractingPath("$.result.like").isEqualTo(1);
        assertThat(call(HttpMethod.PUT, "/api/v1/posts/" + hot + "/like", null, a)).hasStatus2xxSuccessful().bodyJson().extractingPath("$.result.like").isEqualTo(2);
        Map<String, Object> unliked = result(call(HttpMethod.DELETE, "/api/v1/posts/" + hot + "/like", null, b));
        assertThat(unliked.get("like")).isEqualTo(1);
        assertThat(unliked.get("liked")).isEqualTo(false);
        assertThat(call(HttpMethod.DELETE, "/api/v1/posts/" + hot + "/like", null, b)).hasStatus2xxSuccessful().bodyJson().extractingPath("$.result.like").isEqualTo(1);

        List<Map<String, Object>> top = items(result(call(HttpMethod.GET, "/api/v1/posts?scope=hot&size=1", null, "dev:g3")));
        assertThat(top.get(0).get("id")).isEqualTo(hot);
        assertThat(call(HttpMethod.PUT, "/api/v1/posts/999999/like", null, b)).bodyJson().extractingPath("$.code").isEqualTo("POST4001");
        assertThat(call(HttpMethod.GET, "/api/v1/posts/" + cold, null, null)).hasStatus2xxSuccessful().bodyJson().extractingPath("$.result.id").isEqualTo(cold);
    }

    @Test
    void getCountsViewsAndFillsRequesterFieldsOnlyWithAuth() {
        String a = member("p6@example.com", "A6", List.of("069500"));
        String id = (String) post(a, "view me", List.of("069500")).get("id");
        Map<String, Object> anon = result(call(HttpMethod.GET, "/api/v1/posts/" + id, null, null));
        assertThat(anon.get("views")).isEqualTo(1);
        assertThat(anon.get("mine")).isEqualTo(false);
        Map<String, Object> asAuthor = result(call(HttpMethod.GET, "/api/v1/posts/" + id, null, a));
        assertThat(asAuthor.get("views")).isEqualTo(2);
        assertThat(asAuthor.get("mine")).isEqualTo(true);
        assertThat(call(HttpMethod.GET, "/api/v1/posts/abc", null, null)).bodyJson().extractingPath("$.code").isEqualTo("POST4001");
    }

    @Test
    void commentsPageOldestFirstAndRemovalIsAuthorOnly() {
        String a = member("p7@example.com", "A7", List.of("069500"));
        String b = member("p8@example.com", "B7", List.of("069500"));
        String id = (String) post(a, "thread", List.of("069500")).get("id");
        for (String body : List.of("r1", "r2", "r3")) {
            Map<String, Object> comment = result(call(HttpMethod.POST, "/api/v1/posts/" + id + "/comments", Map.of("body", body), b));
            assertThat(((Map<?, ?>) comment.get("author")).get("name")).isEqualTo("B7");
        }
        assertThat(call(HttpMethod.GET, "/api/v1/posts/" + id, null, null)).hasStatus2xxSuccessful().bodyJson().extractingPath("$.result.comment").isEqualTo(3);
        Map<String, Object> page = result(call(HttpMethod.GET, "/api/v1/posts/" + id + "/comments?size=2", null, null));
        assertThat(bodies(page)).isEqualTo(List.of("r1", "r2"));
        Map<String, Object> next = result(call(HttpMethod.GET, "/api/v1/posts/" + id + "/comments?size=2&cursor=" + page.get("nextCursor"), null, null));
        assertThat(bodies(next)).isEqualTo(List.of("r3"));
        assertThat(next.get("nextCursor")).isNull();

        assertThat(call(HttpMethod.DELETE, "/api/v1/posts/" + id, null, b)).hasStatus(403);
        assertThat(call(HttpMethod.DELETE, "/api/v1/posts/" + id, null, a)).hasStatusOk();
        assertThat(call(HttpMethod.GET, "/api/v1/posts/" + id, null, null)).hasStatus(404);
        assertThat(call(HttpMethod.POST, "/api/v1/posts/" + id + "/comments", Map.of("body", "late"), b)).hasStatus(404);
        List<Map<String, Object>> feed = items(result(call(HttpMethod.GET, "/api/v1/posts?code=069500", null, "dev:g4")));
        assertThat(feed).noneMatch(i -> id.equals(i.get("id")));
    }

    @Test
    void commentRemovalIsAuthorOnlyAndDecrementsCount() {
        String a = member("p20@example.com", "A20", List.of("069500"));
        String b = member("p21@example.com", "B20", List.of("069500"));
        String id = (String) post(a, "thread", List.of("069500")).get("id");
        String other = (String) post(a, "other", List.of("069500")).get("id");
        String r1 = read(call(HttpMethod.POST, "/api/v1/posts/" + id + "/comments", Map.of("body", "r1"), b), "$.result.id");
        assertThat(call(HttpMethod.POST, "/api/v1/posts/" + id + "/comments", Map.of("body", "r2"), b)).hasStatus2xxSuccessful();
        String uri = "/api/v1/posts/" + id + "/comments/" + r1;

        assertThat(call(HttpMethod.DELETE, uri, null, null)).hasStatus(401);
        assertThat(call(HttpMethod.DELETE, uri, null, a)).as("글쓴이도 남의 댓글 삭제 불가").hasStatus(403);
        assertThat(call(HttpMethod.DELETE, "/api/v1/posts/" + other + "/comments/" + r1, null, b)).as("다른 글의 댓글")
                .bodyJson().extractingPath("$.code").isEqualTo("POST4004");
        assertThat(call(HttpMethod.DELETE, "/api/v1/posts/" + id + "/comments/abc", null, b))
                .bodyJson().extractingPath("$.code").isEqualTo("POST4004");
        assertThat(call(HttpMethod.DELETE, uri, null, b)).hasStatusOk();
        assertThat(call(HttpMethod.DELETE, uri, null, b)).as("지운 댓글").bodyJson().extractingPath("$.code").isEqualTo("POST4004");

        assertThat(bodies(result(call(HttpMethod.GET, "/api/v1/posts/" + id + "/comments", null, null)))).isEqualTo(List.of("r2"));
        assertThat(call(HttpMethod.GET, "/api/v1/posts/" + id, null, null)).hasStatus2xxSuccessful().bodyJson().extractingPath("$.result.comment").isEqualTo(1);
    }

    // 1.1.0 이전 앱이 부르는 replies 경로와 reply 필드
    @Test
    void legacyReplyPathsAndFieldStillWork() {
        String a = member("p30@example.com", "A30", List.of("069500"));
        String b = member("p31@example.com", "B30", List.of("069500"));
        String id = (String) post(a, "legacy", List.of("069500")).get("id");
        String c = read(call(HttpMethod.POST, "/api/v1/posts/" + id + "/replies", Map.of("body", "old"), b), "$.result.id");
        Map<String, Object> detail = result(call(HttpMethod.GET, "/api/v1/posts/" + id, null, null));
        assertThat(detail.get("comment")).isEqualTo(1);
        assertThat(detail.get("reply")).isEqualTo(1);
        assertThat(ids(result(call(HttpMethod.GET, "/api/v1/posts/" + id + "/replies", null, null)))).isEqualTo(List.of(c));
        assertThat(call(HttpMethod.DELETE, "/api/v1/posts/" + id + "/replies/" + c, null, b)).hasStatusOk();
        assertThat(call(HttpMethod.GET, "/api/v1/posts/" + id, null, null)).hasStatus2xxSuccessful().bodyJson().extractingPath("$.result.comment").isEqualTo(0);
    }

    @Test
    void deletedAuthorIsMasked() {
        String a = member("p9@example.com", "A9", List.of("069500"));
        String id = (String) post(a, "bye", List.of("069500")).get("id");
        assertThat(call(HttpMethod.DELETE, "/api/v1/me", null, a)).hasStatus2xxSuccessful();
        Map<String, Object> post = result(call(HttpMethod.GET, "/api/v1/posts/" + id, null, null));
        assertThat(((Map<?, ?>) post.get("author")).get("name")).isEqualTo("탈퇴한 사용자");
    }

    // 약관 제10조에 따른 글의 익명 보존
    // 투표와 좋아요와 관심의 삭제와 집계 제외
    @Test
    void withdrawalKeepsPostsButRemovesVotesLikesAndWatch() {
        String a = member("p10@example.com", "A10", List.of("069500"));
        String b = member("p11@example.com", "B10", List.of("069500"));
        String id = (String) post(a, "keep", List.of("069500")).get("id");
        assertThat(call(HttpMethod.PUT, "/api/v1/posts/" + id + "/like", null, b)).hasStatus2xxSuccessful();
        assertThat(call(HttpMethod.PUT, "/api/v1/posts/" + id + "/like", null, a)).hasStatus2xxSuccessful();
        assertThat(call(HttpMethod.PUT, "/api/v1/etfs/305720/vote", Map.of("choice", "buy"), b)).hasStatus2xxSuccessful();
        long memberB = jdbc.queryForObject("select id from member where email = 'p11@example.com'", Long.class);

        assertThat(call(HttpMethod.DELETE, "/api/v1/me", null, b)).hasStatus2xxSuccessful();

        Map<String, Object> post = result(call(HttpMethod.GET, "/api/v1/posts/" + id, null, null));
        assertThat(post.get("like")).as("탈퇴 회원 좋아요 차감").isEqualTo(1);
        assertThat(jdbc.queryForObject("select count(*) from vote where member_id = ?", Integer.class, memberB)).isEqualTo(0);
        assertThat(jdbc.queryForObject("select count(*) from post_like where member_id = ?", Integer.class, memberB)).isEqualTo(0);
        assertThat(jdbc.queryForObject("select count(*) from watch_group g join principal p on p.id = g.principal_id where p.member_id = ?",
                Integer.class, memberB)).isEqualTo(0);
        Map<String, Object> count = result(call(HttpMethod.GET, "/api/v1/etfs/305720/vote/count", null, null));
        assertThat(count.get("buys")).as("남은 표가 없는 ETF 의 캐시 교체").isEqualTo(0);
    }

    private MvcTestResult call(HttpMethod method, String uri, Object body, String auth) {
        MockMvcRequestBuilder request = mvcTester.method(method).uri(uri);
        if (auth != null) {
            request.header(auth.startsWith("dev:") ? "X-Device-Id" : "Authorization", auth.startsWith("dev:") ? auth.substring(4) : "Bearer " + auth);
        }
        if (body != null) {
            request.contentType(MediaType.APPLICATION_JSON).content(objectMapper.writeValueAsString(body));
        }
        return request.exchange();
    }

    private String member(String email, String nick, List<String> watched) {
        MemberFixture.plantSignupCode(jdbc, email);
        Map<String, Object> auth = result(call(HttpMethod.POST, "/api/v1/auth/signup",
                Map.of("email", email, "password", "pw123456", "nick", nick, "code", MemberFixture.SIGNUP_CODE), null));
        String token = (String) auth.get("accessToken");
        assertThat(call(HttpMethod.PUT, "/api/v1/watch-groups/base/etfs", Map.of("codes", watched), token)).hasStatus2xxSuccessful();
        return token;
    }

    private Map<String, Object> post(String token, String body, List<String> tags) {
        return result(call(HttpMethod.POST, "/api/v1/posts", Map.of("body", body, "tags", tags), token));
    }

    private static List<Map<String, Object>> items(Map<String, Object> page) {
        return (List<Map<String, Object>>) page.get("items");
    }

    private static List<Object> ids(Map<String, Object> page) {
        return items(page).stream().map(i -> i.get("id")).toList();
    }

    private static List<Object> bodies(Map<String, Object> page) {
        return items(page).stream().map(i -> i.get("body")).toList();
    }
}
