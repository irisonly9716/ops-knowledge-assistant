package com.opsassistant.backend.user;

import lombok.RequiredArgsConstructor;
import org.springframework.core.MethodParameter;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.bind.support.WebDataBinderFactory;
import org.springframework.web.context.request.NativeWebRequest;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.method.support.ModelAndViewContainer;
import org.springframework.web.server.ResponseStatusException;

/**
 * Turns the request's identity into a User for parameters annotated with @CurrentUser. Missing, not a
 * number, or no such user are all 401: the caller is not identified.
 *
 * The one place that knows how a request says who it is. Today that is the X-User-Id header, which is
 * simulated identity: anyone can send any id (see docs/permission-model.md). Switching to verified tokens
 * (issue #5) means changing this class; no endpoint reads the header itself.
 */
@Component
@RequiredArgsConstructor
public class CurrentUserArgumentResolver implements HandlerMethodArgumentResolver {

    static final String HEADER = "X-User-Id";

    private final UserRepository userRepository;

    @Override
    public boolean supportsParameter(MethodParameter parameter) {
        return parameter.hasParameterAnnotation(CurrentUser.class) && parameter.getParameterType() == User.class;
    }

    @Override
    public Object resolveArgument(MethodParameter parameter, ModelAndViewContainer mavContainer,
                                  NativeWebRequest webRequest, WebDataBinderFactory binderFactory) {
        String header = webRequest.getHeader(HEADER);
        if (header == null || header.isBlank()) {
            throw unauthorized("missing " + HEADER + " header");
        }
        try {
            return userRepository.findById(Long.valueOf(header.strip()))
                    .orElseThrow(() -> unauthorized("unknown user: " + header));
        } catch (NumberFormatException e) {
            throw unauthorized(HEADER + " must be a user id, got: " + header);
        }
    }

    private static ResponseStatusException unauthorized(String reason) {
        return new ResponseStatusException(HttpStatus.UNAUTHORIZED, reason);
    }
}
