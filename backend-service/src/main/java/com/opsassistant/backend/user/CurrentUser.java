package com.opsassistant.backend.user;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Marks a controller parameter as "the user making this request". This is the only way endpoints get
 * their caller; nothing else reads the identity from the request (see CurrentUserArgumentResolver).
 */
@Target(ElementType.PARAMETER)
@Retention(RetentionPolicy.RUNTIME)
public @interface CurrentUser {
}
