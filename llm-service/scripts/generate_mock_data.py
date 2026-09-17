"""Day 5:生成模拟数据,插入 departments / users / anchors / campaigns / campaign_metrics。

不接真实阿里数据,自己造。sop_documents / doc_chunks 不在这里造——那两张表要等
Day 9-12 真正的"上传 -> 解析 -> 切片 -> embedding"链路跑起来才有数据,不是假数据。

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

from app.config import settings

fake = Faker("zh_CN")
random.seed(42)  # 固定随机种子:每次跑生成的数据都一样,方便调试和复现问题

# 部门名字是业务领域知识,Faker 编不出来,手写固定列表——
# 这几个名字后面 sop_docs/ 里的 SOP 文档也会用到,必须完全一致(纯字符串匹配,
# 见 docs/permission-model.md:sop_documents.department 不是外键)。
DEPARTMENTS = ["美妆运营", "3C数码运营", "食品生鲜运营", "日用百货运营"]

CAMPAIGN_TEMPLATES = ["618大促", "双11大促", "新品首发", "日常直播专场", "粉丝节", "清仓特卖", "周年庆"]


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
                TRUNCATE departments, users, anchors, campaigns, campaign_metrics
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

            # ---- anchors:每个部门 3-5 个 ----
            for dept_name, dept_id in dept_ids.items():
                n_anchors = random.randint(3, 5)
                cur.executemany(
                    """INSERT INTO anchors (name, department_id, category, fans_count, join_date)
                       VALUES (%s, %s, %s, %s, %s)""",
                    [
                        (
                            fake.name(),
                            dept_id,
                            dept_name.replace("运营", ""),
                            random.randint(1_000, 500_000),
                            fake.date_between(start_date="-2y", end_date="today"),
                        )
                        for _ in range(n_anchors)
                    ],
                )

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

                    cur.execute(
                        """INSERT INTO campaigns (name, department_id, category, start_date, end_date, status)
                           VALUES (%s, %s, %s, %s, %s, %s) RETURNING campaign_id""",
                        (name, dept_id, dept_name.replace("运营", ""), start, end, status),
                    )
                    campaign_id = cur.fetchone()[0]

                    base_gmv = random.uniform(5_000, 50_000)
                    metrics_rows = []
                    for day_offset in range(duration):
                        d = start + timedelta(days=day_offset)
                        if d > date.today():
                            break
                        # 随手加点每日波动,不追求真实,只求"看起来不是常数"
                        wobble = random.uniform(0.6, 1.5)
                        gmv = round(base_gmv * wobble, 2)
                        dau = int(gmv / random.uniform(8, 15))
                        metrics_rows.append((
                            campaign_id, d, gmv, dau,
                            round(random.uniform(15, 90), 2),
                            round(random.uniform(0.01, 0.15), 4),
                        ))
                    cur.executemany(
                        """INSERT INTO campaign_metrics
                           (campaign_id, date, gmv, dau, watch_time, conversion_rate)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        metrics_rows,
                    )

        conn.commit()  # 整个脚本一个事务:中途报错就整体回滚,不会留下"造了一半"的脏数据

    print("✅ 模拟数据生成完毕")


if __name__ == "__main__":
    main()
