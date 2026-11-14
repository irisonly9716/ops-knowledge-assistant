package com.opsassistant.backend.config;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * A line of .env means the same here as in python-dotenv, which llm-service reads it with: a value both services share,
 * such as INTERNAL_SERVICE_TOKEN, must come out the same on both sides or every query is refused.
 */
class DotenvParsingTest {

    @Test
    void a_plain_value_is_the_text_after_the_first_equals_sign() {
        assertThat(DotenvEnvironmentPostProcessor.parse(List.of("TOKEN=abc=def", "  PORT = 5433  ")))
                .containsEntry("TOKEN", "abc=def").containsEntry("PORT", "5433");
    }

    @Test
    void a_non_breaking_space_after_a_value_is_stripped_as_python_strips_it() {
        assertThat(DotenvEnvironmentPostProcessor.parse(List.of("TOKEN=abc ", " OTHER=x")))
                .containsEntry("TOKEN", "abc").containsEntry("OTHER", "x");
    }

    @Test
    void quotes_around_a_value_are_not_part_of_it_and_an_unclosed_one_is_skipped() {
        assertThat(DotenvEnvironmentPostProcessor.parse(List.of("A=\"abc # not a comment\"", "B='x y'", "C=\"unclosed")))
                .containsEntry("A", "abc # not a comment").containsEntry("B", "x y").doesNotContainKey("C");
    }

    @Test
    void a_comment_after_an_unquoted_value_is_dropped_but_a_hash_inside_it_stays() {
        assertThat(DotenvEnvironmentPostProcessor.parse(List.of("A=abc # the token", "B=abc#def", "# C=commented out")))
                .containsEntry("A", "abc").containsEntry("B", "abc#def").doesNotContainKey("C");
    }

    @Test
    void export_in_front_is_dropped_a_name_given_twice_keeps_the_last_value_and_a_name_alone_unsets_it() {
        assertThat(DotenvEnvironmentPostProcessor.parse(List.of("export A=1", "B=first", "B=second", "C=1", "C")))
                .containsEntry("A", "1").containsEntry("B", "second").doesNotContainKey("C");
    }
}
