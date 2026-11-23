---
name: address-check
description: 校核与清洗中文外呼/采集地址，输出可寄送的标准化地址（省/市/区县/详细地址）。支持两类输入：①Excel/文本（含或不含 ASR 通话记录列），跑规则引擎 R1–R9 + 全国行政区划反查 + 拼音方音模糊匹配 + 高德 geocode 兜底；②电话/采集录音（mp3/wav），先离线 ASR 再按四川话/西南官话同近音校核。使用场景：用户要求"地址校核""清洗外呼地址""规范化收货地址""核对省市""处理外呼地址 Excel""听录音核地址""语音转地址""外呼录音地址校核""录音里的地址有错别字"等。
disable-model-invocation: true
---

# 中文地址校核（外呼/采集地址清洗 + 录音校核）

把采集端 / 外呼端拿到的脏地址（省、市、区县、详细地址）规范成可寄送的标准地址。本 skill 是一个完整系统，按输入类型走不同入口，规则与脚本共享同一套行政区划库与方音引擎。

## 输入分流（先判断输入类型）

| 输入 | 走哪条路 |
|---|---|
| Excel/CSV，带地址列（可选带 `通话记录` ASR 列） | **路径 A：批量规则校核**（最常用） |
| Excel 批量校核后仍有"需复核/省市区不全"行，且原表带录音 URL | **路径 B：录音重听深挖** |
| 单个/少量录音文件 mp3/wav | **路径 C：单文件录音校核** |
| 少量（<30）纯文本地址 | 直接由模型按规则 R1–R9 逐条输出，不走脚本 |

所有脚本在 `~/.cursor/skills/address-check/scripts/`，中间产物只写 `/tmp/`。

---

## 路径 A · 批量规则校核（Excel/文本）

> **运行前置检查（强制）·高德 API Key**
> 启动 `batch_verify.py` 之前，**先检查是否有高德 key**（`echo $AMAP_KEY` 或用户是否在本次会话提供 `--amap-key`）。
> - **有 key**：正常带上 `--amap-key`/`AMAP_KEY` 运行，启用 R8。
> - **缺 key**：**不要静默跳过 R8**。先用提问工具向用户确认：①提供高德 key（推荐，可补全区县并减少"需人工复核"）；②本次不调用高德、仅跑离线规则 R1–R7/R9（区县可能补不全）。得到用户选择后再运行。
> 仅当用户已明确"无需高德/离线即可"时，才可在不带 key 的情况下直接运行。

用 `batch_verify.py` 一次完成 R1–R7 + 全国行政区划反查 + 拼音方音模糊匹配 + 镇/街道反查补区县（+ 可选 R8 高德、R9 录音重听）。脚本内置：

- GB/T 2260 全国县/区/县级市数据 (`data/pca-code.json`) + 街道/镇/乡 ~3.3 万条 (`data/pcas-code.json`，懒加载，用于 R5)
- 民族自治州/旗别名表
- `phonetic.py` 西南官话/四川话方音规范化（ong↔ün、ang↔eng、平翘舌、f↔h、n↔l）
- 行数 ≥1000 自动 multiprocessing + openpyxl 流式 I/O

```bash
python3 ~/.cursor/skills/address-check/scripts/batch_verify.py \
  <input.xlsx> [--sheet <sheet>] \
  --prov-col 省 --city-col 市 --addr-col 采集新地址 \
  [--district-col 区/县] [--transcript-col 通话记录] [--id-col 通话记录id] \
  [--name-col 采集姓名] [--phone-col 联系方式] [--gender-col 性别] \
  [--output <out.xlsx>] [--evidence <out.json>] [--workers N] \
  [--amap-key XXX | export AMAP_KEY=XXX] [--amap-scope all|empty|review|none] [--amap-qps 3] \
  [--audio-revisit review|gaps|learn|all|none] [--audio-url-col 通话录音]
```

要点：

- `--district-col 区/县`（推荐）：原表有独立"区县"列时传入。值会合并进 addr 主体供 R2/R3/R5 利用，并单独输出 `原始区县 / 校核区县` 两列。
- `--transcript-col 通话记录`：列里是 ASR 对话（`ME:` 客户原声 / `AI:` 坐席复述），脚本自动做客户原声 vs 坐席复述对照。
- `--amap-scope empty`：只对"校核区县"空的行调高德（最省额度）。**注意：缺 key 时脚本虽会自动跳过 R8，但按上方"运行前置检查"要求，缺 key 必须先与用户确认，不可直接静默跳过。**
- `--audio-revisit`：默认 `review`，对 `review_needed=True` 的行自动下载录音重转写补全（依赖 `data/pcas-code.json` 与离线模型，见路径 C 的 setup）。不需要时传 `none`。
- `--workers 0`（默认）：N≥1000 自动 `min(8,cpu)` 并行，否则串行。

输出 15 列：`id / 手机号 / 采集姓名 / 性别 / 原始省 / 原始市 / 原始区县 / 原始地址 / 校核省 / 校核市 / 校核区县 / 校核地址 / 适用规则 / 状态 / 备注`。黄色=需人工复核，绿色=已自动校核。

> **快捷路径（最常见）**：输入是带 `通话记录`（ASR 已在上游完成，含 ME/AI）的 Excel 时，直接：
> ```bash
> python3 ~/.cursor/skills/address-check/scripts/batch_verify.py \
>   <input.xlsx> --addr-col 采集新地址 --prov-col 省 --city-col 市 \
>   --transcript-col 通话记录 --id-col 通话记录id [--district-col 区/县] [--name-col 采集姓名]
> ```
> 内部自动用 `phonetic.py` 做拼音方音模糊匹配，无需再单独跑文本规则。

---

## 路径 B · 录音重听深挖（针对剩余复核行）

`batch_verify.py` 跑完后仍有 `review_needed=True` 或省/市/区不完整、且原表带录音 URL 列时：

```bash
python3 ~/.cursor/skills/address-check/scripts/revisit_review.py \
  --evidence /tmp/<evidence>.json \
  --input <原表.xlsx> --sheet <sheet> \
  --url-col 通话录音 --id-col 通话记录id \
  --report /tmp/<revisit>.md
```

脚本自动筛选需重听的行（跳过海南直辖县级单位"市空"合法情况）、下载 mp3、跑 SenseVoice int8 重转写、抽地址相关窗口，生成 markdown 报告（本地校核 / 上游 ASR / 重听结果三栏对照），便于裁决并沉淀 R3 typo 字典补丁。

---

## 路径 C · 单文件录音校核（语音 → 校对后地址）

适用于电话外呼录音、客服回访等中文音频（即便 8 kHz 电话音质）。核心思路：离线 ASR 全文转写 → 区分客户原声 vs 坐席复述（**真值在客户原声里**）→ 多窗口/多增益扫描分离稳定字与不稳定字 → 对不稳定字按方音规则生成候选 → 行政区划库交叉验证。

```
任务进度：
- [ ] 1. 环境检查 / 安装依赖与模型
- [ ] 2. 全文 ASR：mp3 → 16 kHz mono → 转写 + 字级时间戳
- [ ] 3. 在转写中定位地址相关时段
- [ ] 4. 抽取客户原声窗口，多滤波/多增益扫描重识别
- [ ] 5. 方音校核：按西南官话/四川话规则生成候选 + 行政区划交叉验证
- [ ] 6. 输出最终标准地址 + 证据链 + 置信度
```

### Step 1 · 环境与模型（执行一次）

```bash
bash ~/.cursor/skills/address-check/scripts/setup.sh
```

安装 `faster-whisper sherpa-onnx av numpy` 并下载 **sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09**（约 158 MB，托管在 GitHub Releases，不要走 huggingface.co）到 `~/.cache/sherpa-onnx-models/`。macOS arch mismatch 排错见 [reference.md §5.1](reference.md)。

### Step 2 · 全文 ASR

```bash
python3 ~/.cursor/skills/address-check/scripts/transcribe.py /path/to/audio.mp3 --output /tmp/asr.log
```

输出完整转写 + 字级时间戳（`12.34s 字`）。自动检测 dual-mono（很多"立体声"电话录音两声道相同），不要做无谓的声道分离。

### Step 3 · 定位地址时段

在转写里找触发词，按时间戳确定窗口。把"客户原声窗口"和"坐席复述窗口"分开记录，**两者差异时以客户原声为准**：

| 模式 | 含义 | 真值在哪 |
|---|---|---|
| `好的您说的是 XX 对吧` | 坐席复述 | 这句**之前**的客户语音 |
| `麻烦您说一下` / `准确送货` | 坐席提问 | 客户在此**之后**几秒回答 |
| `对` / `嗯` / `是的` | 客户确认 | 验证坐席复述与客户原声是否一致 |

### Step 4 · 客户原声多窗口扫描

```bash
python3 ~/.cursor/skills/address-check/scripts/sweep_address.py /path/to/audio.mp3 \
  --centers 118.5,135.5 --output /tmp/sweep.txt
```

`--centers` 是客户讲地址的时间中心点（秒，由 Step 3 决定）。脚本围绕每个中心点用 5 种窗长 × 3 种偏移 × 4 种增强重识别。在多窗口下稳定出现的字 → 可信字；摇摆的字 → 不稳定字，交 Step 5。完整参数见 [reference.md §4](reference.md)。

### Step 5 · 方音校核

对不稳定字按西南官话/四川话同近音规则（ong↔ün、ang↔eng、平翘舌、f↔h、n↔l 等，完整表见 [reference.md §1](reference.md)）列出候选，放回原地址后对照行政区划库（博雅地名网 / 国家统计局 / Wikipedia，渠道见 [reference.md §2](reference.md)）查证：存在且唯一→采用；多个→选与上下文一致者；均不存在→标 `需人工复核`，保留原始字面。

### Step 6 · 输出（两行结论 + 字段表 + 差异行，三段都必须输出）

```
录音字面（ASR 第一版，未校核）：四川省江油市五都镇云凤村4组29号
校核后（建议存档值）：       四川省江油市武都镇永放村4组29号
```

- "录音字面"严格按 ASR 第一版拼接，不做任何方音/常识修正（回归锚点）。
- "校核后"是综合方音规则 + 行政区划库后的最终建议值。即便两行相同也都要输出。

字段表（按字段拆解依据，列：字段 | 录音字面 | 校核后 | 依据/不稳定字候选 | 置信度），再加差异行（仅列变更字段）。

> 把每条差异同步追加到 [examples.md](examples.md) 的"高频错别字字典（供 R3 参考）"——**仅追加新条目**，不改既有条目。

---

## 校核规则 R1–R9（速查）

| 规则 | 作用 |
|---|---|
| R1 | 省份字段规范化（补"省/自治区"后缀；详址有完整省名则回填） |
| R2 | 县级市/县错填为地级市 → 用全国库回填地级市，详址保留县级单位 |
| R3 | 同音/形近错别字纠正（字典命中 + phonetic 模糊匹配兜底） |
| R3b | 镇/街道级 phonetic 兜底（target_county 已锁定时，辖镇内找唯一近似候选） |
| R4 | 删除无关备注/说明/掩码 |
| R5 | 缺县/区时用镇/街道反查补全，并清理伪 county |
| R6 | 折叠冗余重复门牌号 |
| R7 | 详细地址回写校核后的省+市前缀 |
| R8 | 高德 geocode 反查（在线，可选，需 AMAP_KEY） |
| R9 | 录音重听 + 自动学习字典（在线，复核行兜底，沉淀 learned_typo.json） |

**完整规范、决策表、典型案例见 [reference.md §7](reference.md)。** 对照样本与字典见 [examples.md](examples.md)。

---

## 边界与隐私

### 业务边界（默认不越界）

只负责"脏地址/录音 → 校核后的真实可寄送地址"。**默认不要**输出：录音是否疑似诈骗/套路话术、是否建议报送反诈/风控、对客户身份或动机的推断、"风险提示/安全提示/建议……"等业务性结语。下游有自己的业务系统做这些判定。

### 隐私

- 全文转写、扫描、证据 JSON 等中间产物**只写 `/tmp/`**，不持久化到工作区/仓库。
- 最终结果只输出地址，不回放客户姓氏、电话、整段话术等额外个人信息（除非它本身是地址的一部分）。

---

## 工具脚本

| 脚本 | 用途 | 联网 |
|---|---|---|
| `scripts/batch_verify.py` | 端到端校核 Excel/CSV，应用 R1–R9 | R8/R9 时是 |
| `scripts/divisions.py` | 全国行政区划反查（县/区/镇/街道） | 否 |
| `scripts/phonetic.py` | 拼音相似度 + 西南官话方音规范化 | 否 |
| `scripts/poi_geocode.py` | 高德 Web API geocode 客户端 (R8) | 是 |
| `scripts/verify_addresses.py` | 旧版机械清洗（R1/R4/R6/R7），低依赖兜底 | 否 |
| `scripts/audio_revisit.py` | R9 批量录音重听 + 学习字典（batch_verify 内部调用） | 是 |
| `scripts/setup.sh` | 安装 sherpa-onnx + 下载 SenseVoice int8 模型 | 首次是 |
| `scripts/transcribe.py` | 单文件中文 ASR + 字级时间戳 | 否（模型离线） |
| `scripts/sweep_address.py` | 多窗口/多增强 ASR 扫描 | 否 |
| `scripts/revisit_review.py` | 批量重听复核行 → markdown 深挖报告 | 是 |

依赖：`pip3 install --user openpyxl pypinyin`（路径 C 另需 `faster-whisper sherpa-onnx av numpy`，由 setup.sh 装）。
- **可选加速**：装 `python-calamine` 后，`batch_verify.py` 自动用它读 xlsx（比 openpyxl 快 5–20×，1 万行端到端 ~5.5s→~3s）；未装则透明回退 openpyxl。
- **性能特征**：纯规则校核 ~9000 行/秒，瓶颈在读 Excel；≤5 万行默认串行（多进程在此量级是负优化，已由脚本自动判断）。R9 录音重听会先并发下载 + **一次性加载模型批量转写**写入缓存，重跑/续跑命中缓存近乎零成本。

## 参考

- 方音规则全表、ASR 参数、依赖排错、行政区划查证渠道、**文本端规则 R1–R9 详解**：[reference.md](reference.md)
- 真实对照样本、高频错别字字典、县级市→地级市映射：[examples.md](examples.md)
