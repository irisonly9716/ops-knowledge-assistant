package com.opsassistant.backend.upload;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

import com.opsassistant.backend.outbox.OutboxMessage;
import com.opsassistant.backend.outbox.OutboxRepository;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.test.util.ReflectionTestUtils;
import tools.jackson.databind.json.JsonMapper;

/**
 * What UploadService writes. That both rows are committed together (or not at all) needs a real
 * transaction and is tested in OutboxIntegrationTest.
 */
class UploadServiceTest {

    private final UploadRepository uploads = mock(UploadRepository.class);
    private final OutboxRepository outbox = mock(OutboxRepository.class);
    private final JsonMapper json = JsonMapper.builder().build();
    private final UploadService service = new UploadService(uploads, outbox, json);

    @Test
    @SuppressWarnings("unchecked")
    void records_the_upload_and_an_outbox_message_with_the_event_in_the_wire_format() {
        ReflectionTestUtils.setField(service, "uploadTopic", "document-upload-events");
        UploadEvent event = new UploadEvent("u-1", "u-1-x.md", ContentKind.DOCUMENT, DocType.CAMPAIGN_REVIEW, null,
                "Beauty", 1L, null, Source.MANUAL);
        Upload upload = new Upload();

        service.record(upload, event);

        verify(uploads).save(upload);
        ArgumentCaptor<OutboxMessage> message = ArgumentCaptor.forClass(OutboxMessage.class);
        verify(outbox).save(message.capture());
        assertThat(message.getValue().getTopic()).isEqualTo("document-upload-events");
        assertThat(message.getValue().getMessageKey()).isEqualTo("u-1");   // the upload id is the Kafka key
        assertThat(message.getValue().getPublishedAt()).isNull();         // waiting for the relay
        Map<String, Object> payload = json.readValue(message.getValue().getPayload(), Map.class);
        assertThat(payload)
                .containsEntry("uploadId", "u-1")
                .containsEntry("contentKind", "document")        // lowercase, not DOCUMENT
                .containsEntry("docType", "campaign_review")
                .containsEntry("campaignId", 1)
                .containsEntry("tableType", null);               // an explicit null, as in the contract
    }
}
