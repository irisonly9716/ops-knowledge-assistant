"""Generate mock data for departments / users / hosts / campaigns / campaign_metrics /
campaign_products / campaign_customer_behavior.

The last two tables stand in for structured tables that ops staff upload (product sales,
customer behavior). In reality another system computes and exports them; we are not
building an analytics engine, these rows only give downstream code something to query.

documents / doc_chunks are not generated here: SOPs, plans and reviews only get there
through the real upload -> parse -> chunk -> embed pipeline.

Run:
    cd llm-service
    uv run python -m scripts.generate_mock_data

Reproducible: random and Faker are both seeded, and every date is computed from the
fixed AS_OF date instead of today, so each run produces exactly the same rows.
campaign_docs/ quotes numbers from this data; they stay valid as long as this script and
the pinned faker version don't change.

Idempotent: every run empties these tables first (TRUNCATE ... RESTART IDENTITY CASCADE)
so you can rerun it freely. **That is only acceptable because the data is fake** --
a migration of real business data must never do this.
"""
import random
from datetime import date, timedelta

import psycopg
from faker import Faker
from psycopg.types.json import Jsonb

from app.config import settings

SEED = 42
random.seed(SEED)
fake = Faker("en_US")
fake.seed_instance(SEED)

# The data is a snapshot "as of" this date: campaign statuses, the last day of metrics and
# every relative date are computed from it, never from today.
AS_OF = date(2026, 10, 15)

# Department names are domain knowledge Faker can't invent. sop_docs/ and campaign_docs/
# use the same names and must match exactly (plain string matching, see
# docs/permission-model.md: documents.department is not a foreign key).
# Value: (short label used in campaign names, prefix for customer_ref)
DEPARTMENTS = {
    "Beauty": ("Beauty", "BEA"),
    "Consumer Electronics": ("Electronics", "ELE"),
    "Fresh Grocery": ("Grocery", "GRO"),
    "Home Goods": ("Home", "HOM"),
}

# Seasonal campaigns only start inside their window: (month, first day, last day).
# Every department runs each of them once, so "Beauty Black Friday Live" names exactly one campaign.
SEASONAL_CAMPAIGNS = {
    "Black Friday Live": (11, 10, 20),
    "Holiday Sale": (12, 1, 10),
    "Summer Sale": (6, 1, 12),
    "Back to School": (8, 1, 10),
    "Anniversary Sale": (9, 1, 12),
}
# Year-round campaigns can run any time and repeat; their start date tells them apart
EVERGREEN_CAMPAIGNS = ["New Product Launch", "Weekly Live Special", "Fan Appreciation Day", "Clearance Sale"]

# What each department sells -- domain knowledge too, written by hand
PRODUCTS_BY_DEPT = {
    "Beauty": ["Lipstick", "Foundation", "Eyeshadow Palette", "Sheet Mask", "Serum", "Sunscreen",
               "Facial Cleanser"],
    "Consumer Electronics": ["Wireless Earbuds", "Power Bank", "Phone Case", "Smartwatch",
                             "Charging Cable", "Bluetooth Speaker", "Tablet Stand"],
    "Fresh Grocery": ["Fresh Cherries", "Ribeye Steak", "Mixed Nut Gift Box", "Instant Ramen",
                      "Snow Crab Legs", "Organic Veggie Box", "Frozen Dumplings"],
    "Home Goods": ["Paper Towels", "Laundry Detergent", "Trash Bags", "Storage Bins",
                   "Insulated Tumbler", "Spin Mop", "Hanger Set"],
}

REGIONS = ["Northeast", "Southeast", "Midwest", "Southwest", "Mountain West", "West Coast"]

# One fixed customer pool per department; campaigns sample from it, so the same customer
# (same customer_ref) can appear in several campaigns of a department. Without that,
# every campaign has brand-new customers and repeat-purchase rate is always zero.
CUSTOMER_POOL_SIZE = 150


def connect():
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )


def seasonal_start(month: int, first_day: int, last_day: int) -> date:
    """The most recent occurrence of the window that ends at least two weeks before AS_OF."""
    year = AS_OF.year if date(AS_OF.year, month, last_day) <= AS_OF - timedelta(days=14) else AS_OF.year - 1
    return date(year, month, random.randint(first_day, last_day))


def campaign_plan(dept_short: str) -> list[tuple[str, date]]:
    """(name, start date) of every campaign of one department within the year before AS_OF."""
    plan = [(f"{dept_short} {template}", seasonal_start(*window))
            for template, window in SEASONAL_CAMPAIGNS.items()]
    for _ in range(random.randint(5, 10)):
        start = AS_OF - timedelta(days=random.randint(14, 365))
        plan.append((f"{dept_short} {random.choice(EVERGREEN_CAMPAIGNS)}", start))
    return sorted(plan, key=lambda campaign: campaign[1])


def main():
    with connect() as conn:
        with conn.cursor() as cur:
            # RESTART IDENTITY: ids start from 1 again on every run instead of growing
            # CASCADE: departments are referenced by users/hosts/campaigns, so empty those too.
            #   It follows every foreign key: uploads (references users), documents and
            #   doc_chunks (reference campaigns / uploads) are emptied as well, so documents
            #   that were already ingested have to be uploaded again
            cur.execute("""
                TRUNCATE departments, users, hosts, campaigns, campaign_metrics,
                         campaign_products, campaign_customer_behavior
                RESTART IDENTITY CASCADE
            """)

            # ---- departments ----
            dept_ids = {}
            for name in DEPARTMENTS:
                cur.execute(
                    "INSERT INTO departments (name) VALUES (%s) RETURNING department_id",
                    (name,),
                )
                dept_ids[name] = cur.fetchone()[0]

            # ---- users: 5-8 per department, one manager, the rest staff ----
            for dept_name, dept_id in dept_ids.items():
                n_staff = random.randint(4, 7)
                cur.execute(
                    "INSERT INTO users (name, department_id, role) VALUES (%s, %s, 'manager')",
                    (fake.name(), dept_id),
                )
                cur.executemany(
                    "INSERT INTO users (name, department_id, role) VALUES (%s, %s, 'staff')",
                    [(fake.name(), dept_id) for _ in range(n_staff)],
                )
            # Two roles outside every ops department (department_id is NULL, see docs/permission-model.md):
            #   admin     system administrator: sees everything and maintains the system,
            #             never edits business content
            #   executive founder/CEO: sees everything and approves managers' requests, never uploads
            # Fixed names: two special accounts that are easy to recognize
            cur.executemany(
                "INSERT INTO users (name, department_id, role) VALUES (%s, NULL, %s)",
                [("System Administrator", "admin"), ("Founder & CEO", "executive")],
            )

            # ---- hosts: 3-5 per department; keep host_id for campaigns ----
            host_ids_by_dept = {}
            for dept_name, dept_id in dept_ids.items():
                ids = []
                for _ in range(random.randint(3, 5)):
                    cur.execute(
                        """INSERT INTO hosts (name, department_id, category, fans_count, join_date)
                           VALUES (%s, %s, %s, %s, %s) RETURNING host_id""",
                        (
                            fake.name(),
                            dept_id,
                            dept_name,
                            random.randint(1_000, 500_000),
                            fake.date_between(start_date=AS_OF - timedelta(days=730), end_date=AS_OF),
                        ),
                    )
                    ids.append(cur.fetchone()[0])
                host_ids_by_dept[dept_name] = ids

            # ---- campaigns + campaign_metrics: every seasonal campaign plus 5-10 year-round
            #      ones per department, with daily metrics for 15-45 days each ----
            for dept_name, dept_id in dept_ids.items():
                dept_short, customer_prefix = DEPARTMENTS[dept_name]
                customer_pool = [f"CUST-{customer_prefix}-{i:04d}" for i in range(CUSTOMER_POOL_SIZE)]
                for name, start in campaign_plan(dept_short):
                    duration = random.randint(15, 45)
                    end = start + timedelta(days=duration - 1)
                    status = "finished" if end < AS_OF else "running"
                    # The lead host comes from the same department, so filtering by department
                    # never finds a campaign whose host belongs elsewhere
                    host_id = random.choice(host_ids_by_dept[dept_name])

                    # Compute daily GMV first (no campaign_id needed yet) to get total_gmv,
                    # which calibrates operating cost and product sales. Generated
                    # independently, the product sales of a campaign could add up to more
                    # than its GMV in campaign_metrics.
                    base_gmv = random.uniform(5_000, 50_000)
                    daily = []
                    for day_offset in range(duration):
                        d = start + timedelta(days=day_offset)
                        if d > AS_OF:
                            break
                        gmv = round(base_gmv * random.uniform(0.6, 1.5), 2)   # some day-to-day noise
                        dau = int(gmv / random.uniform(8, 15))
                        daily.append((
                            d, gmv, dau,
                            round(random.uniform(15, 90), 2),
                            round(random.uniform(0.01, 0.15), 4),
                            int(dau * random.uniform(1.2, 2.5)),  # peak_concurrent_viewers
                            round(random.uniform(3, 25), 2),      # avg_dwell_time_minutes
                        ))
                    total_gmv = sum(row[1] for row in daily)

                    # Operating cost (placement fees + promotion + giveaways, no product cost):
                    # the plan is a rough share of GMV and the actual cost lands around it --
                    # invoices never match the budget exactly, which is why there are two
                    # fields. Only finished campaigns have an actual cost.
                    planned_cost = round(total_gmv * random.uniform(0.1, 0.2), 2)
                    actual_cost = round(planned_cost * random.uniform(0.85, 1.25), 2) if status == "finished" else None

                    cur.execute(
                        """INSERT INTO campaigns
                           (name, department_id, host_id, category, start_date, end_date, status,
                            planned_operating_cost, actual_operating_cost)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING campaign_id""",
                        (name, dept_id, host_id, dept_name, start, end, status, planned_cost, actual_cost),
                    )
                    campaign_id = cur.fetchone()[0]

                    cur.executemany(
                        """INSERT INTO campaign_metrics
                           (campaign_id, date, gmv, dau, watch_time, conversion_rate,
                            peak_concurrent_viewers, avg_dwell_time_minutes)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        [(campaign_id, *row) for row in daily],
                    )

                    # ---- campaign_products: what this campaign sold (pretending ops uploaded it).
                    # Sales are capped at a share of total_gmv (the rest is long-tail or untracked
                    # products), so the featured products never add up to more than the GMV.
                    # cost_price / profit / profit_margin would come with the uploaded table
                    # (computed by finance), like units_sold; we derive them here only so the
                    # fake numbers agree with each other (profit = sales - product cost).
                    dept_products = PRODUCTS_BY_DEPT[dept_name]
                    campaign_products = random.sample(dept_products, k=random.randint(3, 6))
                    product_pool = total_gmv * random.uniform(0.6, 0.9)
                    weights = [random.random() for _ in campaign_products]
                    weight_total = sum(weights)

                    product_rows = []
                    for product, weight in zip(campaign_products, weights):
                        sales_amount = round(product_pool * weight / weight_total, 2)
                        unit_price = random.uniform(30, 300)
                        units = max(1, round(sales_amount / unit_price))
                        cost_price = round(unit_price * random.uniform(0.4, 0.7), 2)  # 40-70% of the price
                        profit = round(sales_amount - cost_price * units, 2)
                        profit_margin = round(profit / sales_amount, 4) if sales_amount else 0
                        product_rows.append((
                            campaign_id, product, units,
                            round(random.uniform(0.1, 0.95), 4),
                            round(random.uniform(0.01, 0.2), 4),
                            sales_amount,
                            round(random.uniform(0.02, 0.3), 4),
                            cost_price, profit, profit_margin,
                        ))
                    cur.executemany(
                        """INSERT INTO campaign_products
                           (campaign_id, product_name, units_sold, sell_through_rate,
                            return_rate, sales_amount, click_through_rate,
                            cost_price, profit, profit_margin)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        product_rows,
                    )

                    # ---- campaign_customer_behavior: this campaign's customers (also an uploaded table) ----
                    sampled_customers = random.sample(customer_pool, k=random.randint(15, 40))
                    cur.executemany(
                        """INSERT INTO campaign_customer_behavior
                           (campaign_id, customer_ref, region, dwell_time_minutes,
                            products_purchased, purchase_amount, ip_address)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        [
                            (
                                campaign_id, customer_ref, random.choice(REGIONS),
                                round(random.uniform(0.5, 45), 2),
                                Jsonb(random.sample(campaign_products, k=random.randint(1, 3))),
                                round(random.uniform(20, 2000), 2),
                                fake.ipv4(),
                            )
                            for customer_ref in sampled_customers
                        ],
                    )

        conn.commit()  # one transaction for the whole script: an error rolls everything back

    print("Mock data generated")


if __name__ == "__main__":
    main()
