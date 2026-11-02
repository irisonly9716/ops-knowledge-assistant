package com.opsassistant.backend.upload;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.ArgumentMatchers.any;

import com.opsassistant.backend.campaign.CampaignRepository;
import com.opsassistant.backend.department.DepartmentRepository;
import com.opsassistant.backend.document.DocumentRepository;
import com.opsassistant.backend.outbox.OutboxMessage;
import com.opsassistant.backend.outbox.OutboxRepository;
import com.opsassistant.backend.user.UserRepository;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
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
    private final UploadService service = new UploadService(uploads, outbox, mock(DocumentRepository.class),
            mock(CampaignRepository.class), mock(DepartmentRepository.class), mock(DataTableRepository.class),
            mock(UserRepository.class), json);

    @BeforeEach
    void topic() {
        ReflectionTestUtils.setField(service, "uploadTopic", "document-upload-events");
    }

    @Test
    @SuppressWarnings("unchecked")
    void records_the_upload_and_an_outbox_message_with_the_event_in_the_wire_format() {
        Upload upload = upload(Upload.RECEIVED);

        service.record(upload);

        verify(uploads).save(upload);
        ArgumentCaptor<OutboxMessage> message = ArgumentCaptor.forClass(OutboxMessage.class);
        verify(outbox).save(message.capture());
        String id = upload.getUploadId().toString();
        assertThat(message.getValue().getTopic()).isEqualTo("document-upload-events");
        assertThat(message.getValue().getMessageKey()).isEqualTo(id);     // the upload id is the Kafka key
        assertThat(message.getValue().getPublishedAt()).isNull();         // waiting for the relay
        Map<String, Object> payload = json.readValue(message.getValue().getPayload(), Map.class);
        assertThat(payload)
                .containsEntry("uploadId", id)
                .containsEntry("storageKey", id + "-x.md")
                .containsEntry("contentKind", "document")        // lowercase, not DOCUMENT
                .containsEntry("docType", "campaign_review")
                .containsEntry("department", "Beauty")
                .containsEntry("campaignId", 1)
                .containsEntry("category", "Compliance")
                .containsEntry("source", "manual")
                .containsEntry("tableType", null);               // an explicit null, as in the contract
    }

    @Test
    void an_upload_waiting_for_approval_has_no_event() {
        Upload upload = upload(Upload.PENDING_APPROVAL);

        service.record(upload);

        verify(uploads).save(upload);
        verify(outbox, never()).save(any());
    }

    private static Upload upload(String status) {
        UUID id = UUID.randomUUID();
        Upload upload = new Upload();
        upload.setUploadId(id);
        upload.setStorageKey(id + "-x.md");
        upload.setSource("manual");
        upload.setContentKind("document");
        upload.setDocType("campaign_review");
        upload.setDepartment("Beauty");
        upload.setCampaignId(1L);
        upload.setCategory("Compliance");
        upload.changeStatus(status, null);
        return upload;
    }
}
