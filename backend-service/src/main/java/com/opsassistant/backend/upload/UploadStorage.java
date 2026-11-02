package com.opsassistant.backend.upload;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;

/** Where uploaded files live: UPLOAD_DIR today, S3 later. Everything else only knows the storage key. */
@Slf4j
@Component
public class UploadStorage {

    @Value("${document.upload-dir}")
    private String uploadDir;

    /** Stores the file and returns its storage key (the file name inside the upload folder). */
    public String save(MultipartFile file, UUID uploadId) throws IOException {
        Path dir = Path.of(uploadDir);
        Files.createDirectories(dir);
        // Prefix the uploadId so two people uploading the same file name never overwrite each other
        String storageKey = uploadId + "-" + sanitizeFilename(file.getOriginalFilename());
        file.transferTo(dir.resolve(storageKey));
        return storageKey;
    }

    /** Removes a file nobody will use. If the delete itself fails, only log it; it must not mask the original outcome. */
    public void deleteQuietly(String storageKey) {
        try {
            Files.deleteIfExists(Path.of(uploadDir).resolve(storageKey));
        } catch (IOException e) {
            log.warn("failed to remove orphan file, delete it by hand: {}", storageKey, e);
        }
    }

    /**
     * getOriginalFilename() is WHATEVER THE CLIENT SENT: it can be "../../evil.md", "C:\x\y.md",
     * contain control characters, or be null. Putting it straight into a file name on disk is the
     * classic path traversal hole. Path separators, characters Windows forbids and control
     * characters are all replaced with underscores, giving a string that can only ever be a
     * single-level file name on any operating system. Ordinary characters, including non-ASCII
     * letters, are kept.
     * Note: replace, don't "take the last segment" -- that misses Windows-style backslash paths on Linux.
     */
    static String sanitizeFilename(String original) {
        if (original == null) {
            return "upload";
        }
        String cleaned = original.replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]", "_").strip();
        if (cleaned.length() > 100) {
            cleaned = cleaned.substring(cleaned.length() - 100); // keep the end so the extension survives
        }
        return cleaned.isBlank() || cleaned.chars().allMatch(c -> c == '.') ? "upload" : cleaned;
    }
}
