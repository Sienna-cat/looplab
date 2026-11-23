# LoopLab · 营销评测工作台

## 在线 Demo（无需安装）

**[点击直接操作 Looplab →](https://sienna-cat.github.io/looplab/)**

[作品集与四个 Demo 总入口](https://sienna-cat.github.io/drivefollow/portfolio/#demos) · [公开源码](https://github.com/Sienna-cat/looplab)

打开网址即可体验，无需登录、下载代码或启动本地服务。使用合成数据与浏览器规则演示，不调用真实电话、企微或大模型；操作仅在当前浏览器内保存，不会影响其他访客。DriveReach 的 40 个规则场景会在浏览器中实际执行。

更新 `main` 后，GitHub Actions 会运行测试并自动部署。可用 `npm run build:static` 生成 `dist/` 静态站点。


可运行的 Web Demo 与作品集说明页。默认地址：http://127.0.0.1:4317 。

## 快速启动（独立项目版）

需要 [Node.js 24 LTS](https://nodejs.org/) 或更高版本，以及 Chrome、Edge、Firefox 或 Safari。四个项目都没有第三方运行依赖，无需 `npm install`，也不需要安装 Codex。

1. 下载或解压整个 `looplab` 文件夹。
2. 在此目录打开终端，执行：

```sh
npm start
```

3. 浏览器打开 <http://127.0.0.1:4317>。终端保持运行；结束时按 Ctrl+C。

macOS 可双击 `启动演示.command`（权限提示时在终端执行 `sh start.sh`）；Windows 可双击 `start.bat`；Linux/macOS 可执行 `sh start.sh`。脚本在本机兼容 Codex 自带 Node，其他电脑安装标准 Node.js 即可。

端口占用时，复制 `.env.example` 为 `.env`，修改 `PORT` 后重新执行 `npm start`。`.env` 只留在本地，不提交 GitHub。

```sh
npm test
```

交付验证见 [docs/交付验证.md](docs/交付验证.md)。历史设计和旧验证保留原日期，不能当成本次重新验证。

## 演示路径

1. 概览 → 运行完整演示，完成客群开发评测与核单。
2. 客群泛化 → 比较二维/三维和最小样本门槛 → 查看验证集 → 冻结策略并测试 → 导出 JSON。
3. 智能核单 → 比较 v1/v2 → 筛选待人工 → 查看原始地址、建议与处理轨迹 → 填依据并保存人工裁决。
4. 下载 CSV 模板 → 导入本地文件 → 批量处理。未知地名转人工，未标注数据不显示正确率。
5. 作品集说明 → 展示问题定义、职责边界、评测方案、历史证据与 Bad Case → 浏览器打印为 PDF。

## 数据与边界

- 6,000 条客户记录由固定随机种子生成，客户标识互不重复；60% 训练、20% 验证、20% 测试。模拟转化率为展示效果设计，不代表实际业务水平。
- 24 条地址记录全部为虚构案例，真实区县名配合虚构详细地址；示例真值在独立 gold 对象中，仅评分层读取。
- 地址引擎为有限范围的本地演示适配器：覆盖余杭区、西湖区、锦江区、武侯区、浦东新区、鼓楼区、南山区、海淀区。未接生产脚本、ASR、地图或投放接口，规则通过不代表地址真实存在或可寄送。
- 历史 3,985 条批次统计与二维/三维留出 lift 以静态证据单独展示；来源是 docs/history/ 已复核的作品集材料。本 Demo 未重新处理真实客户原始数据。
- 示例人工裁决按规则版本保存在当前浏览器 localStorage，导入的地址和裁决只保留在内存；刷新后导入内容消失。需留存时导出 JSON。
- 首次查看与重复使用测试集的标记保存在当前浏览器。再次测试明确标为探索，不冒充新的独立验收。清理浏览器存储不产生新数据集。
- 所有操作均在本地执行，不上传文件，不需要 API Key。

## CSV 格式

必需列：`订单ID,原始地址`。可选列：`通话证据,省,市,区县`。UTF-8，支持 BOM、带引号的逗号和换行；最大 1 MB、1,000 条；重复或缺失订单 ID 拒绝导入。地址为空的行会被保留并转人工。

## 验证与构建

```bash
node --test tests/engine.test.mjs
node build.mjs
node server.mjs --dist
```

`dist/` 可托管为普通静态站点。无需构建也可以直接运行源站点。ES modules 需通过 HTTP 打开，不支持双击 index.html 的 file 协议。

## 文件职责

- `public/engine.mjs`：核单逻辑、指标、规则冻结、CSV 解析。
- `public/data.mjs`：模拟数据、独立示例真值及历史汇总。
- `public/views.mjs`、`public/app.mjs`：页面呈现与交互状态。
- `public/styles.css`：桌面、移动和打印布局。
- `tests/engine.test.mjs`：业务风险与数据泄漏回归测试。


## 独立交付内容与后续 GitHub

本目录包含网页、业务逻辑、演示数据、测试、设计文档和现有验证材料。全部运行路径在目录内解析。数据来自合成样例；官方产品知识为带来源和采集日期的静态摘要。

- `start.sh` / `启动演示.command` / `start.bat`：macOS、Linux、Windows 启动入口。
- `package.json`：标准启动和测试命令；无第三方运行依赖，因此不需要锁文件。
- `.gitignore`：排除密钥、缓存、安装目录及运行数据库；`.env.example` 可提交。
- `docs/项目案例.md`：与整份作品集的关系、职责和演示重点。
- `docs/交付验证.md`：本次拆分后的验证结果。

本目录是独立 GitHub 仓库的项目根目录。仓库地址：[Sienna-cat/looplab](https://github.com/Sienna-cat/looplab)。上传仓库只提供源码下载；要让面试官点击公网网址直接演示，还需后续托管。默认规则模式不需要 API Key，也不会拨打电话或发送企微消息。

### 历史工具资料

`docs/history/` 保留历史设计和汇总报告，`reference-sources/` 保留相关 Python/Skill 源文件供审阅。网页运行完全独立，不需要这些历史工具。历史原始客户数据、录音、密钥、第三方模型权重和个人缓存未纳入交付；历史脚本需要另配环境和授权输入，不能用网页模拟数据冒充原业务复跑。详见 [历史源码说明](reference-sources/README.md)。
