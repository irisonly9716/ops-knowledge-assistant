package com.opsassistant.backend.upload;

import static org.assertj.core.api.Assertions.assertThat;

import com.opsassistant.backend.user.User;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

/** The permission matrix of docs/permission-model.md, row by row. */
class UploadPolicyTest {

    private static final long BEAUTY = 1;
    private static final long GROCERY = 2;

    private final User staff = user(1, "staff", BEAUTY);
    private final User otherStaff = user(2, "staff", BEAUTY);
    private final User groceryStaff = user(3, "staff", GROCERY);
    private final User manager = user(4, "manager", BEAUTY);
    private final User otherManager = user(5, "manager", BEAUTY);
    private final User groceryManager = user(6, "manager", GROCERY);
    private final User executive = user(7, "executive", null);
    private final User admin = user(8, "admin", null);

    @ParameterizedTest
    @CsvSource({"staff, true", "manager, true", "executive, false", "admin, false"})
    void only_departments_upload(String role, boolean allowed) {
        assertThat(UploadPolicy.canUpload(user(1, role, BEAUTY))).isEqualTo(allowed);
    }

    @ParameterizedTest
    @CsvSource({
            "false, Beauty, false",   // new, own department: takes effect at once
            "true,  Beauty, true",    // replacing anything
            "false, ALL,    true",    // affects everyone
            "true,  ALL,    true",
    })
    void what_needs_approval(boolean replacing, String department, boolean expected) {
        assertThat(UploadPolicy.needsApproval(replacing, department)).isEqualTo(expected);
    }

    @Test
    void a_manager_reviews_the_staff_of_their_own_department_only() {
        assertThat(UploadPolicy.canReview(manager, staff)).isTrue();
        assertThat(UploadPolicy.canReview(manager, groceryStaff)).isFalse();
        assertThat(UploadPolicy.canReview(groceryManager, staff)).isFalse();
    }

    @Test
    void a_manager_reviews_no_manager_not_even_themselves() {
        assertThat(UploadPolicy.canReview(manager, manager)).isFalse();
        assertThat(UploadPolicy.canReview(manager, otherManager)).isFalse();
        assertThat(UploadPolicy.canReview(groceryManager, manager)).isFalse();
    }

    @Test
    void the_executive_reviews_managers_and_not_staff() {
        assertThat(UploadPolicy.canReview(executive, manager)).isTrue();
        assertThat(UploadPolicy.canReview(executive, groceryManager)).isTrue();
        assertThat(UploadPolicy.canReview(executive, staff)).isFalse();
    }

    @Test
    void staff_and_admin_review_nothing() {
        for (User uploader : new User[] {staff, otherStaff, manager, executive}) {
            assertThat(UploadPolicy.canReview(otherStaff, uploader)).isFalse();
            assertThat(UploadPolicy.canReview(admin, uploader)).isFalse();
        }
    }

    @Test
    void a_manager_with_no_department_reviews_nobody() {
        User uploaderWithNoDepartment = user(9, "staff", null);
        assertThat(UploadPolicy.canReview(user(10, "manager", null), uploaderWithNoDepartment)).isFalse();
    }

    @Test
    void the_status_is_visible_to_the_uploader_the_reviewer_and_admin() {
        assertThat(UploadPolicy.canSeeStatus(staff, staff)).isTrue();
        assertThat(UploadPolicy.canSeeStatus(manager, staff)).isTrue();
        assertThat(UploadPolicy.canSeeStatus(executive, manager)).isTrue();
        assertThat(UploadPolicy.canSeeStatus(admin, staff)).isTrue();
    }

    @Test
    void the_status_is_hidden_from_everyone_else() {
        assertThat(UploadPolicy.canSeeStatus(otherStaff, staff)).isFalse();
        assertThat(UploadPolicy.canSeeStatus(groceryManager, staff)).isFalse();
        assertThat(UploadPolicy.canSeeStatus(executive, staff)).isFalse();   // system data, like the audit log
    }

    private static User user(long id, String role, Long departmentId) {
        return new User(id, role + " " + id, departmentId, role);
    }
}
