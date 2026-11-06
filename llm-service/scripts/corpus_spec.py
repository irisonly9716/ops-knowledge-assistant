"""What the evaluation corpus contains: the SOPs to write and the campaigns to write plans and reviews for.

The corpus exists so that retrieval has something to get wrong. Every staff member should see far more chunks than
the top 5, and some of them should look like the right answer without being it:
- the same SOP for several departments: same title pattern and structure, different rules (the `like` field names
  the existing document whose structure a new one follows)
- the same kind of document for two years: the 2025 and 2026 Black Friday readiness checklists
- a general rule and an exception to it: prices lock 24 hours before a stream, 48 hours for Black Friday 2026
- campaigns with the same name, told apart only by their dates: three overlapping Home New Product Launch campaigns
  in January 2026, two Beauty Fan Appreciation Day campaigns that both start in February 2026
- campaigns with only one of the two documents, so "what did the review say" can have no answer

A brief holds every number an SOP may contain: the generator rejects a number that isn't in it, so each fact a
question can ask about is known in advance. Campaign documents get their numbers from the database instead.
"""
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class SopSpec:
    filename: str
    title: str
    department: str          # a department name, or ALL
    category: str
    responsible_team: str
    brief: tuple[str, ...]   # what the SOP must say, with every number it may contain
    like: str | None = None  # an existing SOP in sop_docs/ whose structure this one follows


@dataclass(frozen=True)
class CampaignSpec:
    department: str
    name: str
    start: date
    plan: bool
    review: bool
    plan_file: str | None = None     # set when the file already exists under another name
    review_file: str | None = None


ALL = "ALL"
BEAUTY = "Beauty"
ELECTRONICS = "Consumer Electronics"
GROCERY = "Fresh Grocery"
HOME = "Home Goods"

# The SOPs already in sop_docs/, written by hand. They count toward the corpus and are not generated again
EXISTING_SOPS = {
    "account-restriction-handling.md": ALL,
    "new-campaign-launch-checklist.md": ALL,
    "livestream-review-process-beauty.md": BEAUTY,
    "host-onboarding-and-review-electronics.md": ELECTRONICS,
    "sale-event-inventory-check-fresh-grocery.md": GROCERY,
}

LIVESTREAM_REVIEW = "livestream-review-process-beauty.md"
HOST_ONBOARDING = "host-onboarding-and-review-electronics.md"
INVENTORY_CHECK = "sale-event-inventory-check-fresh-grocery.md"

SOPS = (
    # ---- company-wide
    SopSpec("refund-and-return-requests.md", "Refund and Return Requests", ALL, "Customer Service",
            "Customer Service team", (
                "Customers can return an item within 30 days of delivery",
                "Perishable food can't be returned unless it arrived spoiled; the Fresh Grocery spoilage claim process applies",
                "The refund is issued within 5 business days after the returned item is received",
                "An item bought at a livestream-only price is refunded at the price the customer paid, not the list price",
                "A refund above $500 needs the sign-off of a Customer Service team lead",
                "Every request gets a reason code: wrong item, damaged, changed mind or quality issue",
                "Hosts and moderators never promise a refund in the live chat; they point the customer to the order page",
            )),
    SopSpec("livestream-incident-response.md", "Livestream Incident Response", ALL, "Live Operations",
            "Live Operations team", (
                "Covers a stream that drops, a wrong price on screen, a product that sells out mid-stream and a raid of offensive comments",
                "If the stream drops, the producer switches to the backup stream within 2 minutes while the host keeps talking",
                "A wrong price: remove the product link at once",
                "Orders placed at a wrong price are honored if the error is less than 30% off the right price; otherwise they are cancelled with an apology coupon of $10",
                "A product that sells out: the host announces it and moves to the next product; no waitlist is promised on air",
                "Offensive comments: the moderator turns on slow mode and blocks the accounts; the host doesn't respond to them",
                "The incident log is filed within 1 hour after the stream ends, and a short post-incident note goes out the next day",
            )),
    SopSpec("promotional-pricing-and-discount-approval.md", "Promotional Pricing and Discount Approval", ALL, "Pricing",
            "Pricing team", (
                "A discount of up to 15% is approved by the department manager",
                "A discount between 15% and 30% needs the Pricing team's approval",
                "A discount above 30% needs the finance director's approval",
                "No promotional price may go below the product's cost price",
                "Prices are locked 24 hours before a stream; after that, no price change without the Pricing team",
                "A bundle discount counts as the combined discount of the bundle, not per item",
            )),
    SopSpec("host-scheduling-and-shift-handover.md", "Host Scheduling and Shift Handover", ALL, "Host management",
            "Live Operations team", (
                "The schedule for the next week is published every Thursday",
                "A host is on camera for at most 4 hours a day",
                "Two hosts overlap for 15 minutes at a handover",
                "The handover sheet lists the stock status, promises made in the chat that are still open, and any complaints",
                "A shift swap needs 48 hours' notice and the Live Operations team's confirmation",
            )),
    SopSpec("customer-complaint-escalation.md", "Customer Complaint Escalation", ALL, "Customer Service",
            "Customer Service team", (
                "Level 1: a support agent answers the complaint within 4 hours",
                "Level 2: a team lead takes over if the complaint is unresolved after 24 hours, or if the customer threatens a public review",
                "Level 3: Trust & Safety and Legal take over for an injury, a product safety issue or a regulator's inquiry",
                "Compensation is never discussed in public comments, only in a private message",
                "Every complaint is logged in the CRM with its complaint ID",
            )),
    SopSpec("black-friday-readiness-checklist-2025.md", "Black Friday Readiness Checklist 2025", ALL, "Launch checklist",
            "Ops team", (
                "Black Friday 2025 is on November 28, 2025",
                "Advertising budgets are approved by October 31, 2025",
                "The stream schedule is final by November 7, 2025",
                "Inventory is frozen on November 14, 2025: no stock moves between warehouses after that",
                "The checkout is load tested on November 18, 2025",
                "The war room is staffed from 8 am to midnight during Black Friday week",
                "New in 2025: a second backup stream encoder for every studio",
            )),
    SopSpec("black-friday-readiness-checklist-2026.md", "Black Friday Readiness Checklist 2026", ALL, "Launch checklist",
            "Ops team", (
                "Black Friday 2026 is on November 27, 2026",
                "Advertising budgets are approved by October 23, 2026, a week earlier than in 2025, because seasonal campaigns ran over budget",
                "Seasonal campaign budgets include a 10% buffer for rising ad bids",
                "The stream schedule is final by November 6, 2026",
                "Inventory is frozen on November 13, 2026",
                "Prices are locked 48 hours before each Black Friday stream, instead of the usual 24 hours",
                "The war room is staffed from 7 am to 1 am during Black Friday week",
            )),
    SopSpec("campaign-review-writing-guidelines.md", "Campaign Review Writing Guidelines", ALL, "Review",
            "Ops Analytics team", (
                "A post-campaign review is due within 10 business days after the campaign ends",
                "Sections: results against targets, ROI, product breakdown, recommendations for similar campaigns",
                "ROI is the total product profit divided by the actual operating cost; product profit already has the cost of goods taken off, so it must not be subtracted again",
                "Numbers come from the uploaded data tables, never estimated by hand",
                "A result inside the target range is on target, below it is below target, above it is above target",
                "An operating cost more than 5% over the plan is flagged in bold and explained",
            )),

    # ---- Beauty
    SopSpec("sale-event-inventory-check-beauty.md", "Sale Event Inventory Check (Beauty)", BEAUTY, "Inventory",
            "Beauty Supply Chain team", (
                "The check is done 72 hours before the stream",
                "No product with less than 6 months of shelf life left is put on sale",
                "Lipstick and foundation are checked shade by shade: a shade with fewer than 50 units is taken out of the stream",
                "Testers and free samples are counted separately from sellable stock",
                "Temperature-sensitive serums are stored below 25°C",
                "The Beauty category manager signs off the check",
            ), like=INVENTORY_CHECK),
    SopSpec("product-claims-and-compliance-review-beauty.md", "Product Claims and Compliance Review (Beauty)", BEAUTY,
            "Compliance", "Beauty Compliance team", (
                "Scripts must not say \"cures\", \"medical grade\", \"guaranteed anti-aging\" or \"100% natural\" unless the product is certified",
                "An SPF claim needs the product's test report",
                "Before-and-after photos need the brand's written consent and must not be retouched",
                "The script is reviewed 3 business days before the stream",
                "When a skincare product is introduced, its ingredient list stays on screen for 10 seconds",
            )),
    SopSpec("sample-and-gift-bundle-handling-beauty.md", "Sample and Gift Bundle Handling (Beauty)", BEAUTY, "Fulfillment",
            "Beauty Operations team", (
                "Gift-with-purchase thresholds are set in the campaign plan",
                "An order gets at most 5 samples",
                "Gift stock is reserved separately from sellable stock",
                "If a gift runs out, it is replaced with a gift of equal or higher value and the host announces the change",
                "Samples are never charged for",
                "If a bundle is returned without its gift, $5 per missing gift is deducted from the refund",
            )),
    SopSpec("host-onboarding-and-review-beauty.md", "Host Onboarding and Review (Beauty)", BEAUTY, "Host management",
            "Beauty Live team", (
                "A new host does 3 trial streams of 1 hour each",
                "The skincare knowledge quiz has a pass mark of 80%",
                "Every new host completes shade-matching training before streaming color cosmetics",
                "The monthly review compares the host's conversion rate with the department average",
                "A host's return rate should stay below 15%",
                "Probation lasts 60 days",
            ), like=HOST_ONBOARDING),

    # ---- Consumer Electronics
    SopSpec("post-livestream-review-process-consumer-electronics.md", "Post-Livestream Review Process (Consumer Electronics)",
            ELECTRONICS, "Review", "Electronics Data team", (
                "Every campaign is reviewed within 48 hours after it ends",
                "Core numbers: conversion rate, demo-to-purchase rate, and returns marked \"not as described\"",
                "The host, the product specialist and the data analyst attend the review",
                "Findings go into the Electronics review tracker",
                "A wrong product spec shown on screen is corrected at the start of the next stream",
            ), like=LIVESTREAM_REVIEW),
    SopSpec("sale-event-inventory-check-consumer-electronics.md", "Sale Event Inventory Check (Consumer Electronics)",
            ELECTRONICS, "Inventory", "Electronics Supply Chain team", (
                "The check is done 5 days before the stream",
                "Every unit's serial number is scanned",
                "All units of a product must run the same firmware version",
                "Power banks above 100 Wh can't ship by air",
                "If the previous batch had a defect rate above 2%, the batch is held for inspection",
            ), like=INVENTORY_CHECK),
    SopSpec("warranty-and-defect-returns-consumer-electronics.md", "Warranty and Defect Returns (Consumer Electronics)",
            ELECTRONICS, "Customer Service", "Electronics After-Sales team", (
                "Every product has a 12-month warranty",
                "A unit that is dead on arrival within 7 days is replaced, not repaired",
                "The customer sends a video that shows the defect",
                "A repair takes at most 10 business days",
                "Refurbished units are never sold in a livestream",
            )),
    SopSpec("product-demo-setup-for-livestreams-consumer-electronics.md",
            "Product Demo Setup for Livestreams (Consumer Electronics)", ELECTRONICS, "Live production",
            "Electronics Live team", (
                "Demo units are fully charged and on the latest firmware",
                "There is a backup unit for every product that is demonstrated",
                "Screen mirroring is tested 30 minutes before the stream",
                "Hosts never compare the product with a competitor's by name",
                "Earbuds and speakers are demonstrated below 85 dB",
            )),

    # ---- Fresh Grocery
    SopSpec("post-livestream-review-process-fresh-grocery.md", "Post-Livestream Review Process (Fresh Grocery)", GROCERY,
            "Review", "Fresh Grocery Data team", (
                "The review happens the morning after the stream, by 10 am",
                "Core numbers: the spoilage complaint rate and the sell-through of each perishable product",
                "The host and the supply chain planner attend the review",
                "Perishable stock left over is moved to other channels within 24 hours",
            ), like=LIVESTREAM_REVIEW),
    SopSpec("cold-chain-shipping-and-spoilage-claims-fresh-grocery.md",
            "Cold Chain Shipping and Spoilage Claims (Fresh Grocery)", GROCERY, "Fulfillment",
            "Fresh Grocery Logistics team", (
                "Frozen items ship with dry ice and spend at most 48 hours in transit",
                "Frozen items don't ship on Fridays, so they don't sit in a depot over the weekend",
                "A spoilage claim is made within 24 hours of delivery, with a photo",
                "The customer chooses between a refund and a new shipment",
                "If claims exceed 3% of a shipment, the carrier is reviewed",
            )),
    SopSpec("food-safety-labeling-check-fresh-grocery.md", "Food Safety Labeling Check (Fresh Grocery)", GROCERY,
            "Compliance", "Fresh Grocery Quality team", (
                "Allergens such as nuts and shellfish are shown on screen when the product is introduced",
                "Chilled food has at least 10 days left before its best-before date when it ships",
                "Dry goods have at least 90 days left before their best-before date when they ship",
                "The country of origin is stated for meat and seafood",
                "An organic claim needs the certificate number",
            )),
    SopSpec("host-onboarding-and-review-fresh-grocery.md", "Host Onboarding and Review (Fresh Grocery)", GROCERY,
            "Host management", "Fresh Grocery Live team", (
                "Every new host completes food handling training",
                "Raw meat and raw seafood are never tasted on camera",
                "A new host does 2 trial streams",
                "The review looks at the spoilage complaint rate and the conversion rate",
                "Probation lasts 45 days",
            ), like=HOST_ONBOARDING),

    # ---- Home Goods
    SopSpec("post-livestream-review-process-home-goods.md", "Post-Livestream Review Process (Home Goods)", HOME, "Review",
            "Home Goods Data team", (
                "Streams are reviewed together once a week, every Monday, covering the week before, instead of one by one",
                "Core numbers: average order value, bundle attach rate and damage-in-transit complaints",
                "The host and the merchandiser attend the review",
            ), like=LIVESTREAM_REVIEW),
    SopSpec("sale-event-inventory-check-home-goods.md", "Sale Event Inventory Check (Home Goods)", HOME, "Inventory",
            "Home Goods Supply Chain team", (
                "The check is done 7 days before the stream",
                "Bulky items need confirmed warehouse space",
                "5% of cartons are opened to check for damage",
                "A multi-pack counts as a single SKU",
                "Stock is kept 10% above the forecast",
            ), like=INVENTORY_CHECK),
    SopSpec("host-onboarding-and-review-home-goods.md", "Host Onboarding and Review (Home Goods)", HOME, "Host management",
            "Home Goods Live team", (
                "New hosts practise product demos such as assembling storage bins and using a spin mop",
                "A new host does 2 trial streams of 90 minutes each",
                "The review compares the host's average order value with the department average",
                "Probation lasts 90 days",
            ), like=HOST_ONBOARDING),
    SopSpec("bulky-item-delivery-and-damage-claims-home-goods.md", "Bulky Item Delivery and Damage Claims (Home Goods)",
            HOME, "Fulfillment", "Home Goods Logistics team", (
                "Items over 30 lb ship by freight",
                "The delivery window is booked with the customer",
                "A damage claim is made within 48 hours, with photos of the carton and of the item",
                "Cosmetic damage gets a partial refund of 20%",
                "Damage that stops the item from working gets a replacement",
            )),
    SopSpec("bundle-pricing-rules-home-goods.md", "Bundle Pricing Rules (Home Goods)", HOME, "Pricing",
            "Home Goods Merchandising team", (
                "A bundle has 2 to 4 items",
                "A bundle discount is at most 25%, and above 15% it needs the Pricing team's approval",
                "Every bundle includes at least one item with a margin above 35%",
                "Clearance items are never bundled",
            )),
)

# 20 seasonal campaigns, one of each kind per department, and 10 regular ones chosen because their names repeat.
# Selected by department, name and start date, which is how the campaign is identified without its database id
CAMPAIGNS = (
    CampaignSpec(BEAUTY, "Beauty Black Friday Live", date(2025, 11, 18), plan=True, review=True,
                 plan_file="beauty-black-friday-live-plan.md", review_file="beauty-black-friday-live-review.md"),
    CampaignSpec(BEAUTY, "Beauty Holiday Sale", date(2025, 12, 4), plan=False, review=True),
    CampaignSpec(BEAUTY, "Beauty Summer Sale", date(2026, 6, 12), plan=True, review=True),
    CampaignSpec(BEAUTY, "Beauty Back to School", date(2026, 8, 9), plan=True, review=True),
    CampaignSpec(BEAUTY, "Beauty Anniversary Sale", date(2026, 9, 7), plan=True, review=True),
    CampaignSpec(BEAUTY, "Beauty Fan Appreciation Day", date(2026, 2, 14), plan=True, review=True),
    CampaignSpec(BEAUTY, "Beauty Fan Appreciation Day", date(2026, 2, 27), plan=True, review=True),
    CampaignSpec(BEAUTY, "Beauty Weekly Live Special", date(2026, 9, 28), plan=True, review=False),   # still running
    CampaignSpec(ELECTRONICS, "Electronics Black Friday Live", date(2025, 11, 16), plan=True, review=True),
    CampaignSpec(ELECTRONICS, "Electronics Holiday Sale", date(2025, 12, 6), plan=True, review=True),
    CampaignSpec(ELECTRONICS, "Electronics Summer Sale", date(2026, 6, 10), plan=True, review=True),
    CampaignSpec(ELECTRONICS, "Electronics Back to School", date(2026, 8, 10), plan=True, review=True),
    CampaignSpec(ELECTRONICS, "Electronics Anniversary Sale", date(2026, 9, 2), plan=False, review=True),
    CampaignSpec(ELECTRONICS, "Electronics New Product Launch", date(2025, 12, 10), plan=True, review=True),
    CampaignSpec(ELECTRONICS, "Electronics New Product Launch", date(2026, 1, 8), plan=True, review=True),
    CampaignSpec(GROCERY, "Grocery Black Friday Live", date(2025, 11, 12), plan=True, review=True),
    CampaignSpec(GROCERY, "Grocery Holiday Sale", date(2025, 12, 7), plan=False, review=True),
    CampaignSpec(GROCERY, "Grocery Summer Sale", date(2026, 6, 9), plan=True, review=True),
    CampaignSpec(GROCERY, "Grocery Back to School", date(2026, 8, 6), plan=True, review=True),
    CampaignSpec(GROCERY, "Grocery Anniversary Sale", date(2026, 9, 10), plan=True, review=True),
    CampaignSpec(GROCERY, "Grocery Fan Appreciation Day", date(2025, 10, 28), plan=True, review=True),
    CampaignSpec(GROCERY, "Grocery Fan Appreciation Day", date(2025, 11, 16), plan=True, review=True),
    CampaignSpec(HOME, "Home Black Friday Live", date(2025, 11, 18), plan=True, review=True),
    CampaignSpec(HOME, "Home Holiday Sale", date(2025, 12, 3), plan=True, review=True),
    CampaignSpec(HOME, "Home Summer Sale", date(2026, 6, 10), plan=True, review=True),
    CampaignSpec(HOME, "Home Back to School", date(2026, 8, 3), plan=True, review=True),
    CampaignSpec(HOME, "Home Anniversary Sale", date(2026, 9, 4), plan=True, review=True),
    CampaignSpec(HOME, "Home New Product Launch", date(2026, 1, 8), plan=True, review=True),
    CampaignSpec(HOME, "Home New Product Launch", date(2026, 1, 18), plan=True, review=True),
    CampaignSpec(HOME, "Home New Product Launch", date(2026, 1, 24), plan=True, review=False),   # review not written yet
)

# A first batch to judge the quality by before generating the rest: one document of every author but the standard
# one, which the hand-written samples already show, including a departmental twin and two campaigns of one name
PILOT = (
    "host-scheduling-and-shift-handover.md",                 # terse
    "sample-and-gift-bundle-handling-beauty.md",              # faq
    "post-livestream-review-process-fresh-grocery.md",        # explainer, on the template of the Beauty one
    "grocery-black-friday-live-2025-11-12-plan.md",           # memo
    "home-new-product-launch-2026-01-08-plan.md",             # brief
    "grocery-black-friday-live-2025-11-12-review.md",         # recap
    "home-new-product-launch-2026-01-08-review.md",           # story
    "home-new-product-launch-2026-01-18-review.md",           # wins
)


# ---------------------------------------------------------------------------
# Who writes what. Real documents come from many people: one writes fragments, another long paragraphs, and one
# document mixes a paragraph of 150 words with one of a single line. The first batch, all written to one template,
# came out with every paragraph about the same length. So each document gets an author: a way of writing and the
# sections that author uses. Each department has two people who write most of its SOPs, as departments do.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Section:
    heading: str
    guidance: str
    placeholders: tuple[str, ...] = ()   # blocks inserted in this section, in this order


@dataclass(frozen=True)
class Author:
    name: str
    style: str                    # how this person writes, as the model is told
    sections: tuple[Section, ...]
    words: tuple[int, int]        # not counting the inserted blocks
    mixed_paragraphs: bool = False   # must have a paragraph of 100 words or more and one of 20 or fewer
    example: bool = False         # shown the hand-written sample, whose style it shares


TEMPLATE_SECTIONS = (   # the company template the hand-written SOPs follow; a set of departmental twins keeps it
    Section("When to Use", "when this procedure applies"),
    Section("Procedure", "the numbered steps"),
    Section("Partner Teams", "which team helps with what"),
)

SOP_AUTHORS = {
    "standard": Author("standard", "Writes like the example: a short opening paragraph, numbered steps that each start "
                       "with a bold name, nested bullets where a step has cases, a few bullets on partner teams.",
                       TEMPLATE_SECTIONS, (250, 450), example=True),
    "terse": Author("terse", "A busy team lead who writes in fragments: short bullets and numbered steps of one line "
                    "each where possible, almost no full sentences. Puts an 'Owner:' line and an 'Applies to:' line "
                    "right under the title.", (
                        Section("Scope", "a few bullets"),
                        Section("Steps", "numbered steps, most of them a single line"),
                        Section("Contacts", "bullets of the form 'team: what for'"),
                    ), (110, 300)),
    "explainer": Author("explainer", "Writes in full paragraphs and explains why each rule exists. The paragraphs vary "
                        "a lot in length: one long paragraph of well over 100 words gives the background, and several "
                        "paragraphs are a single sentence. Uses a short numbered list only for the core steps.", (
                            Section("Purpose", "why this procedure exists"),
                            Section("How It Works", "paragraphs, with one short numbered list of the core steps"),
                            Section("Common Mistakes", "what people get wrong"),
                            Section("Who to Ask", "one or two sentences"),
                        ), (380, 700), mixed_paragraphs=True),
    "formal": Author("formal", "Writes formal policy text: defined terms in bold, numbered steps with lettered "
                     "sub-steps (a, b, c), and a closing notes section. Some steps are one line, others a full paragraph.", (
                         Section("Purpose", "one or two sentences"),
                         Section("Definitions", "a few terms in bold, each with a one-line definition"),
                         Section("Procedure", "numbered steps with lettered sub-steps"),
                         Section("Notes", "anything that doesn't fit a step"),
                     ), (250, 600)),
    "faq": Author("faq", "Writes the document as answers to the questions colleagues actually ask. Each question is a "
                  "### heading; some answers are one line, others a full paragraph.", (
                      Section("Overview", "two or three sentences"),
                      Section("Questions and Answers", "5 to 8 questions, each a ### heading"),
                      Section("Escalation", "one short paragraph"),
                  ), (250, 600)),
}
# The sections of a twin are the template's, so only authors whose style fits them write one
TEMPLATE_AUTHORS = ("standard", "terse", "explainer", "formal")
SOP_HOUSE_AUTHORS = {   # the five hand-written SOPs are all in the standard style already
    ALL: ("formal", "terse"),
    BEAUTY: ("explainer", "faq"),
    ELECTRONICS: ("terse", "formal"),
    GROCERY: ("explainer", "terse"),
    HOME: ("faq", "standard"),
}

PLAN_AUTHORS = {
    "standard": Author("standard", "Writes like the example: short sections, one bullet per product.", (
        Section("Background", "2 to 4 sentences: what the campaign is, when it runs, who hosts it, which products"),
        Section("Product Lineup", "one bullet per product: its role, such as hero, volume or add-on product, and why"),
        Section("Pricing and Promotions", "one bullet per product, with the promotion given in the facts"),
        Section("Targets (for the post-campaign review)", "", ("{{TARGETS}}",)),
        Section("Risks", "2 or 3 bullets, without numbers"),
    ), (200, 450), example=True),
    "brief": Author("brief", "Writes the plan as a one-page brief in bullets and fragments, no paragraph longer than "
                    "two lines.", (
                        Section("Goal", "one or two lines"),
                        Section("Products and Offers", "one bullet per product with its promotion"),
                        Section("Targets", "", ("{{TARGETS}}",)),
                        Section("Watch-outs", "a few bullets"),
                    ), (90, 280)),
    "memo": Author("memo", "Writes the plan as a memo to the team, in paragraphs: a long opening paragraph of over 100 "
                   "words on why the campaign exists and how it should feel on stream, then shorter paragraphs, some of "
                   "a single sentence. Bullets only for the offers.", (
                       Section("Why This Campaign", "a long paragraph, then a short one"),
                       Section("Lineup and Offers", "a paragraph on the lineup, then one bullet per product with its promotion"),
                       Section("Targets", "the block, then one sentence", ("{{TARGETS}}",)),
                       Section("Risks and Mitigations", "short paragraphs"),
                   ), (300, 650), mixed_paragraphs=True),
}

REVIEW_AUTHORS = {
    "standard": Author("standard", "Writes like the example: a sentence or two after each table, numbered findings "
                       "that start with a bold sentence, a few bullets of recommendations.", (
                           Section("Results vs Targets", "1 or 2 sentences on the overall result", ("{{RESULTS_TABLE}}",)),
                           Section("ROI", "one sentence on what this ROI means", ("{{ROI_BLOCK}}",)),
                           Section("Product Breakdown", "**Findings**: 3 or 4 numbered findings, each starting with a "
                                   "bold sentence, quoting numbers from the facts", ("{{PRODUCT_TABLE}}",)),
                           Section("Recommendations for Similar Campaigns", "2 to 4 bullets, without numbers"),
                       ), (200, 550), example=True),
    "recap": Author("recap", "Writes a short recap for busy readers: a two-line summary, the numbers with a one-line "
                    "label before each block, and a few bullets.", (
                        Section("Summary", "two lines at most"),
                        Section("Numbers", "a one-line label before each block",
                                ("{{RESULTS_TABLE}}", "{{ROI_BLOCK}}", "{{PRODUCT_TABLE}}")),
                        Section("Next Time", "3 bullets"),
                    ), (60, 260)),
    "story": Author("story", "Tells how the campaign went the way a producer would, in paragraphs, before getting to the "
                    "numbers. Mixes a long paragraph of over 100 words with paragraphs of a single sentence.", (
                        Section("How It Went", "a long paragraph, then a one-sentence one"),
                        Section("Results", "a short paragraph after the blocks", ("{{RESULTS_TABLE}}", "{{ROI_BLOCK}}")),
                        Section("Products", "one or two paragraphs on the products", ("{{PRODUCT_TABLE}}",)),
                        Section("Lessons", "short paragraphs"),
                    ), (350, 750), mixed_paragraphs=True),
    "wins": Author("wins", "Organizes the review around what worked and what didn't, in bullets: some of them a few "
                   "words, others several sentences.", (
                       Section("Overview", "one paragraph"),
                       Section("Results", "", ("{{RESULTS_TABLE}}", "{{ROI_BLOCK}}", "{{PRODUCT_TABLE}}")),
                       Section("What Worked", "bullets"),
                       Section("What Didn't", "bullets"),
                       Section("Actions", "numbered"),
                   ), (180, 520)),
}
