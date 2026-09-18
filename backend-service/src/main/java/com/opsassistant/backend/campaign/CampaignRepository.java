package com.opsassistant.backend.campaign;

import org.springframework.data.jpa.repository.JpaRepository;

public interface CampaignRepository extends JpaRepository<Campaign, Long> {
    // existsById(Long) 是 JpaRepository 自带的,不用自己写——
    // UploadController 校验 campaignId 是否存在,直接调这个就够了。
}
