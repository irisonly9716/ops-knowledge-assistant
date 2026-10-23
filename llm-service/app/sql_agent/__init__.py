# Text-to-SQL。schema 拼 prompt → 生成 SQL → 安全校验(只允许 SELECT)
#             → 注入 department 行级过滤 → 执行 → 结果翻译成自然语言。
