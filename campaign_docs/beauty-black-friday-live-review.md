---
doc_type: campaign_review
title: Beauty Black Friday Live - Post-Campaign Review
department: Beauty
campaign_name: Beauty Black Friday Live
host: Tommy Walter
---

# Beauty Black Friday Live - Post-Campaign Review

## Results vs Targets

| Metric | Target (see the campaign plan) | Actual | Verdict |
|---|---|---|---|
| GMV | $500K-$700K | $600.4K | On target |
| Conversion rate | 6%-8% | 8.62% | Above target |
| Peak concurrent viewers | 5,000-7,000 | 6,488 | On target |
| Operating cost | Planned $71.1K | Actual $73.6K | **3.5% over budget**; ad bids rose during Black Friday, the risk the plan flagged |

## ROI (the formula changed; here is why)

**The first two versions were wrong.** The first one subtracted only the operating cost, not
the product cost, so ROI looked too high. The second one added the product cost to the
denominator, which is also wrong: **product cost is already deducted in
`campaign_products.profit`** (profit = sales - product cost; the customer pays for the goods,
it is not money the company spent on this campaign). The real question is: **how much profit
did the marketing spend (the operating cost) bring in?**

- Total product profit (sum of `profit` over the 3 featured products in `campaign_products`, product cost already deducted): **$168.8K**
- Actual operating cost (`campaigns.actual_operating_cost`): **$73.6K**
- **ROI = total product profit / operating cost = 168.8 / 73.6 ≈ 2.29**: every $1 of marketing spend brought back about $2.29 of product profit

Both numbers now come from structured fields (`SUM(campaign_products.profit)` and
`campaigns.actual_operating_cost`), so the review no longer estimates them by hand or
improvises a formula.

## Product Breakdown

| Product | Units sold | Sales | Profit | Margin | Return rate |
|---|---|---|---|---|---|
| Sheet Mask | 470 | $131.6K | $45.4K | 34.52% | 10.2% |
| Lipstick | 528 | $127.6K | $59.2K | 46.36% | 19.2% |
| Serum | 796 | $110.1K | $64.2K | 58.26% | 19.7% |

**Findings**:

1. **Serum had the highest margin (58.26%), the most units and the most profit.** The plan
   positioned it as the traffic driver; it turned out to be the most profitable product as
   well. A winter skincare staple does not need a deep discount to sell.
2. **Sheet masks had the highest sales but the lowest margin (34.52%).** The "20% off two
   boxes" bundle drove volume and gave away most of the margin.
3. **Lipstick and serum came back at almost twice the return rate of sheet masks (about 19% vs 10%).**
   For lipstick this is probably shade mismatch; worth checking the return reasons before
   the next campaign.
4. **Operating cost was 3.5% over budget**, the overrun the plan warned about ("ad placement
   bids usually rise during Black Friday"). It was small this time, but it points the same
   way as other seasonal campaigns.

## Recommendations for Similar Campaigns

- Budget seasonal campaigns with a buffer based on the past overrun of actual over planned cost,
  instead of the ratio used for regular campaigns
- Keep serum as the hero product, and test a smaller bundle discount on sheet masks to win back margin
- Look into lipstick returns (shade guidance during the stream, swatch close-ups) before featuring it again
