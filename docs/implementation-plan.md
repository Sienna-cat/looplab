# LoopLab Implementation Plan

**Goal:** 交付本地运行的营销评测 Demo 和作品集页。
**Architecture:** 数据生成与真值、业务规则、指标计算、UI 分离。静态页面通过原生 ES modules 调用本地规则，无外部接口。
**Tech Stack:** Node 20+、原生 JavaScript、CSS、SVG、node:test。
**Spec:** design.md。

## 任务顺序

1. 核心逻辑：先为地址错配、空地址、未标注指标、分组隔离、冻结规则和 CSV 解析编写失败测试，再实现 `public/engine.mjs` 和 `public/data.mjs`。执行 `node --test` 验证。
2. 可运行产品：实现 `public/index.html`、`public/styles.css`、`public/app.mjs`、`public/views.mjs`，完成四页和真实交互。`node server.mjs` 启动，`node build.mjs` 输出静态站点。
3. 用户交付：README 和双击启动入口，说明模拟范围与评测证据。生成导入模板和 JSON 下载。
4. 浏览器验证：桌面与手机检查排版；执行策略开发/冻结、核单详情/人工确认、CSV 导入与导出；检查控制台与页面状态。修复后重跑失败项。

## 关键接口

- `checkAddress(record, version)` 返回 `status, suggested, changed, reasons, trace`，不接受 gold。
- `evaluateOrders(records, predictions, gold)` 返回带分母的放行错误率、覆盖率、召回、纠错精确率和误改率；无真值时质量指标为 null。
- `developStrategy(customers, config)` 仅用训练集建立规则，用验证集报告；返回 selectedKeys 和固定配置。
- `testStrategy(customers, frozen)` 用已选规则评估测试集，不重新选择。
- `parseAddressCSV(text)` 验证表头、ID 唯一性和行数，返回未标注记录。

不改动原始 Skill，不复制客户电话、姓名、录音、密钥或真实地址到网页。
