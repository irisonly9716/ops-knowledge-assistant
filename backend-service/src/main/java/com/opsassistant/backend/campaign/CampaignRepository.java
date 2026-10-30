package com.opsassistant.backend.campaign;

import org.springframework.data.jpa.repository.JpaRepository;

public interface CampaignRepository extends JpaRepository<Campaign, Long> {
    // existsById(Long) comes with JpaRepository; it is all UploadController needs
    // to check that a campaignId exists.
}
