package com.opsassistant.backend.department;

import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

/**
 * The departments table from docs/data-schema.md, one of the base tables of the permission model
 * (every campaign / host / user belongs to a department).
 *
 * Lombok's @Getter/@Setter/constructor annotations save writing getXxx()/setXxx() by hand: with the
 * lombok dependency in pom.xml (and the Lombok plugin in the IDE) they are generated at compile time.
 */
@Entity
@Table(name = "departments")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
public class Department {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long departmentId;

    private String name;
}
