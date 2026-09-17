"""Day 5:生成模拟数据,插入 departments / users / anchors / campaigns / campaign_metrics /
campaign_products / campaign_customer_behavior。

后两张表模拟的是"运营上传的结构化表格"(商品售卖数据、顾客行为数据)——现实中这些
本来就该是别的系统算好/导出给我们的,不是我们自己造的分析引擎,这里只是伪造几行
方便下游代码有数据可查。

不接真实阿里数据,自己造。documents / doc_chunks 不在这里造——那两张表(SOP、
策划案、复盘文档)要等 Day 6+ 真正的"上传 -> 解析 -> 切片 -> embedding"链路
跑起来才有数据,不是假数据。

跑法:
    cd llm-service
    uv run python -m scripts.generate_mock_data

幂等:每次跑都会先清空这几张表再重新生成(TRUNCATE ... RESTART IDENTITY CASCADE),
方便反复调整数据量重跑。**这只是因为这些数据本来就是假的、可以随便丢**——
真实业务数据的迁移脚本绝不能这样写。
"""
import random
from datetime import date, timedelta

import psycopg
from faker import Faker
from psycopg.types.json import Jsonb

from app.config import settings

fake = Faker("zh_CN")
random.seed(42)  # 固定随机种子:每次跑生成的数据都一样,方便调试和复现问题

# 部门名字是业务领域知识,Faker 编不出来,手写固定列表——
# 这几个名字后面 sop_docs/ 里的 SOP 文档、以及策划案/复盘文档也会用到,必须
# 完全一致(纯字符串匹配,见 docs/permission-model.md:documents.department 不是外键)。
DEPARTMENTS = ["美妆运营", "3C数码运营", "食品生鲜运营", "日用百货运营"]

CAMPAIGN_TEMPLATES = ["618大促", "双11大促", "新品首发", "日常直播专场", "粉丝节", "清仓特卖", "周年庆"]

# 每个部门卖的商品,也是业务领域知识,手写
PRODUCTS_BY_DEPT = {
    "美妆运营": ["口红", "粉底液", "眼影盘", "面膜", "精华液", "防晒霜", "洗面奶"],
    "3C数码运营": ["蓝牙耳机", "充电宝", "手机壳", "智能手表", "数据线", "音箱", "平板支架"],
    "食品生鲜运营": ["车厘子", "牛肉卷", "坚果礼盒", "螺蛳粉", "大闸蟹", "有机蔬菜包", "速冻水饺"],
    "日用百货运营": ["抽纸", "洗衣液", "垃圾袋", "收纳箱", "保温杯", "拖把", "衣架套装"],
}

REGIONS = ["华东", "华南", "华北", "华中", "西南", "东北"]

# 每个部门一个固定的"顾客池",campaign 之间从池子里抽样——这样同一个顾客
# (同一个 customer_ref)有机会出现在这个部门的多场 campaign 里,以后想算
# "复购率"才有意义(不然每场活动的顾客都是全新的,永远查不出复购)。
CUSTOMER_POOL_SIZE = 150


def connect():
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )


def main():
    with connect() as conn:
        with conn.cursor() as cur:
            # RESTART IDENTITY:自增 id 也归零,重跑几次 id 都从 1 开始,不会越跑越大
            # CASCADE:department 被 users/anchors/campaigns 外键引用,级联清空
            cur.execute("""
                TRUNCATE departments, users, anchors, campaigns, campaign_metrics,
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

            # ---- users:每个部门 5-8 人,一个 manager,其余 staff;
            #      第一个部门额外插一个 admin,用来测试"admin 能看全部部门" ----
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
            first_dept_id = next(iter(dept_ids.values()))
            cur.execute(
                "INSERT INTO users (name, department_id, role) VALUES (%s, %s, 'admin')",
                ("系统管理员", first_dept_id),
            )

            # ---- anchors:每个部门 3-5 个,记下 anchor_id 给 campaigns 用 ----
            anchor_ids_by_dept = {}
            for dept_name, dept_id in dept_ids.items():
                n_anchors = random.randint(3, 5)
                ids = []
                for _ in range(n_anchors):
                    cur.execute(
                        """INSERT INTO anchors (name, department_id, category, fans_count, join_date)
                           VALUES (%s, %s, %s, %s, %s) RETURNING anchor_id""",
                        (
                            fake.name(),
                            dept_id,
                            dept_name.replace("运营", ""),
                            random.randint(1_000, 500_000),
                            fake.date_between(start_date="-2y", end_date="today"),
                        ),
                    )
                    ids.append(cur.fetchone()[0])
                anchor_ids_by_dept[dept_name] = ids

            # ---- 每个部门一个固定顾客池,供 campaign_customer_behavior 抽样 ----
            customer_pool_by_dept = {
                dept_name: [f"CUST-{dept_name[:2]}-{i:04d}" for i in range(CUSTOMER_POOL_SIZE)]
                for dept_name in dept_ids
            }

            # ---- campaigns + campaign_metrics:每个部门 10-15 个活动,
            #      每个活动 15-60 天的每日指标 ----
            for dept_name, dept_id in dept_ids.items():
                n_campaigns = random.randint(10, 15)
                for _ in range(n_campaigns):
                    duration = random.randint(15, 60)
                    start = fake.date_between(start_date="-1y", end_date="-2M")
                    end = start + timedelta(days=duration)
                    status = "finished" if end < date.today() else random.choice(["running", "planned"])
                    name = f"{dept_name.replace('运营', '')}{random.choice(CAMPAIGN_TEMPLATES)}"
                    # 从本部门的主播里挑一个当主责主播——不跨部门挑,保持
                    # campaign 的部门和主播的部门一致(避免以后按部门过滤时数据自相矛盾)
                    anchor_id = random.choice(anchor_ids_by_dept[dept_name])

                    # 先把每日 gmv 算出来(还不需要 campaign_id),这样才能拿到 total_gmv
                    # 去校准运营成本和商品销售额——不然三张表的数字会各算各的、互相对不上
                    # (之前 campaign_products 的销售额加起来会超过 campaign_metrics 的 GMV,
                    # 就是因为两边完全独立生成,没有互相校准)。
                    base_gmv = random.uniform(5_000, 50_000)
                    daily = []
                    for day_offset in range(duration):
                        d = start + timedelta(days=day_offset)
                        if d > date.today():
                            break
                        wobble = random.uniform(0.6, 1.5)  # 随手加点每日波动,不追求真实
                        gmv = round(base_gmv * wobble, 2)
                        dau = int(gmv / random.uniform(8, 15))
                        daily.append((
                            d, gmv, dau,
                            round(random.uniform(15, 90), 2),
                            round(random.uniform(0.01, 0.15), 4),
                            int(dau * random.uniform(1.2, 2.5)),  # peak_concurrent_viewers
                            round(random.uniform(3, 25), 2),      # avg_dwell_time_minutes
                        ))
                    total_gmv = sum(row[1] for row in daily)

                    # 运营成本(坑位费+推广+赠品,不含商品成本):按 GMV 的一个粗略比例定计划成本,
                    # 实际成本在计划上下浮动——活动结束账单和预算本来就会有出入,这正是要分开
                    # 记两个字段的原因。只有"finished"的活动才填得出实际成本,没跑完的还不知道。
                    planned_cost = round(total_gmv * random.uniform(0.1, 0.2), 2)
                    actual_cost = round(planned_cost * random.uniform(0.85, 1.25), 2) if status == "finished" else None

                    cur.execute(
                        """INSERT INTO campaigns
                           (name, department_id, anchor_id, category, start_date, end_date, status,
                            planned_operating_cost, actual_operating_cost)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING campaign_id""",
                        (name, dept_id, anchor_id, dept_name.replace("运营", ""), start, end, status,
                         planned_cost, actual_cost),
                    )
                    campaign_id = cur.fetchone()[0]

                    cur.executemany(
                        """INSERT INTO campaign_metrics
                           (campaign_id, date, gmv, dau, watch_time, conversion_rate,
                            peak_concurrent_viewers, avg_dwell_time_minutes)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        [(campaign_id, *row) for row in daily],
                    )

                    # ---- campaign_products:这场活动卖了哪些商品(运营上传的表,假装是)。
                    # 销售额校准到 total_gmv 的一个比例以内(留一部分给长尾/未追踪的商品),
                    # 保证"主推品销售额加起来"永远不会超过这场活动的总 GMV。
                    # cost_price/profit/profit_margin 现实中应该跟 units_sold 这些一样,
                    # 是这份上传表格自带的字段(财务系统算好的),不是我们衍生计算的——
                    # 这里只是为了让假数据内部数字对得上(profit = 销售额 - 商品成本),才顺手推一下。
                    dept_products = PRODUCTS_BY_DEPT[dept_name]
                    campaign_products = random.sample(dept_products, k=min(len(dept_products), random.randint(3, 6)))
                    product_pool = total_gmv * random.uniform(0.6, 0.9)
                    weights = [random.random() for _ in campaign_products]
                    weight_total = sum(weights)

                    product_rows = []
                    for product, weight in zip(campaign_products, weights):
                        sales_amount = round(product_pool * weight / weight_total, 2)
                        unit_price = random.uniform(30, 300)
                        units = max(1, round(sales_amount / unit_price))
                        cost_price = round(unit_price * random.uniform(0.4, 0.7), 2)  # 成本价是售价的 40%-70%
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

                    # ---- campaign_customer_behavior:这场活动的顾客行为(同样是运营上传的表) ----
                    sampled_customers = random.sample(
                        customer_pool_by_dept[dept_name],
                        k=min(len(customer_pool_by_dept[dept_name]), random.randint(15, 40)),
                    )
                    cur.executemany(
                        """INSERT INTO campaign_customer_behavior
                           (campaign_id, customer_ref, region, dwell_time_minutes,
                            products_purchased, purchase_amount, ip_address)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        [
                            (
                                campaign_id, customer_ref, random.choice(REGIONS),
                                round(random.uniform(0.5, 45), 2),
                                Jsonb(random.sample(campaign_products, k=min(len(campaign_products), random.randint(1, 3)))),
                                round(random.uniform(20, 2000), 2),
                                fake.ipv4(),
                            )
                            for customer_ref in sampled_customers
                        ],
                    )

        conn.commit()  # 整个脚本一个事务:中途报错就整体回滚,不会留下"造了一半"的脏数据

    print("✅ 模拟数据生成完毕")


if __name__ == "__main__":
    main()
