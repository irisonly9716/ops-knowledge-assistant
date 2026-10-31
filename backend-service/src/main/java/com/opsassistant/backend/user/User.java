package com.opsassistant.backend.user;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

/**
 * The users table. There is no login: a request names its user in the X-User-Id header, and this
 * entity is how the backend finds out who that is (see docs/permission-model.md). Users are created
 * by the mock data script, not through backend-service, hence no @GeneratedValue.
 */
@Entity
@Table(name = "users")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
public class User {

    @Id
    private Long userId;

    private String name;

    private Long departmentId;   // NULL for executive and admin, who belong to no department

    private String role;         // staff / manager / executive / admin
}
