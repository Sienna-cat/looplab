# address-check · 参考资料

## 1. 西南官话 / 四川话音变规则

按"对真实地名识别错误最有用"的次序排列。每条给出"原 → 误识"方向（即客户原音 → ASR/听写出来的字）的常见对应。

### 1.1 韵母合并

| 规则 | 方向 | 在四川话出现的范围 | 典型字对 |
|---|---|---|---|
| ong ↔ ün | 永 yǒng → 云 yún；东 dōng → 顿 dùn | 全四川（最普遍） | **永/云、宗/尊、东/顿** |
| ang ↔ eng | 放 fàng → 凤 fèng；上 shàng → 圣 shèng | 全四川 | **放/凤、上/胜、王/翁** |
| an ↔ en | 蓝 lán → 林 lín | 部分地区（成渝差异） | 蓝/林、丹/敦 |
| in ↔ ing | 因 yīn → 英 yīng | 全四川 | 因/英、信/兴 |
| en ↔ eng | 真 zhēn → 蒸 zhēng | 全四川 | 真/蒸、奔/崩 |

### 1.2 声母合并

| 规则 | 方向 | 范围 | 典型 |
|---|---|---|---|
| 平翘舌不分（zh/ch/sh = z/c/s） | 春 chūn → 村 cūn；主 zhǔ → 组 zǔ；上 shàng → 桑 sāng | 全四川 | **村/春、组/主、市/四** |
| n ↔ l | 牛 niú → 流 liú；女 nǚ → 吕 lǚ | 多数地区 | 牛/流、女/吕 |
| f ↔ h | 风 fēng → 哄 hōng；福 fú → 胡 hú | 主要岷江话区（自贡、乐山一带） | 风/哄、福/胡 |
| r → y / z | 人 rén → 银 yín；让 ràng → 样 yàng | 部分地区 | 人/银、日/二 |

### 1.3 入声归阳平 / 声调合流

电话失真 + ASR 普通话训练数据，实际表现为**声调高度不可靠**——把"凤 fèng"和"风 fēng / 丰 fēng"看作同一组候选。

### 1.4 应用模板

对一个不稳定字段（如客户的村名第一字）：

```
原音： yǒng (假设)
依次套用音变规则:
  ong↔ün → 候选 +{ yún }
  在四川话部分点 yong 实际读 [yn] → 与 yún 几乎不可分
  声调合流 → 候选 +{ yùn, yǔn }
落地候选集 = { 永, 云, 用, 永, 雍, 永, 涌, 涌 } ∪ { 韵, 运, 孕 } 等
```

然后用行政区划库筛掉不存在的，剩下的就是真值候选。

## 2. 行政区划查证渠道（按可信度从高到低）

| 渠道 | URL 模式 | 适用 | 备注 |
|---|---|---|---|
| 民政部 · 行政区划代码 | `https://www.mca.gov.cn/n156/n186/...` | 县级以上 | 最权威，但只到县级 |
| 国家统计局 · 统计用区划和城乡划分代码 | `https://www.stats.gov.cn/sj/tjbz/qhdm/` | 到村委会 / 居委会级 | 12 位 GB/T 2260 代码，最适合村级查证 |
| 博雅地名网 | `http://tcmap.com.cn/<拼音>` 或 `http://m.xfwindow.com/<拼音>` | 镇 / 村 / 社区 | 含历史沿革，便于追溯合并前的老村名 |
| Wikipedia 中文 | `https://zh.wikipedia.org/zh-cn/<地名>` | 镇 / 部分村 | 有英文拼音便于消歧 |
| 北斗地图地名查询 | `https://www.bdditu.com/...` | 村级 | 含行政代码、人口、面积 |

调用顺序建议：

1. **博雅地名网**列出该镇所有村，看候选是否在其中。
2. 若不在，用**国家统计局**接口按行政代码逐级搜索（村级最权威）。
3. 若仍不在，再到 Wikipedia / 百度百科 / 北斗地图旁证。

## 3. ASR 模型选择与参数

### 推荐模型

| 模型 | 大小 | 中文准确度 | 备注 |
|---|---|---|---|
| **sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09** | 158 MB | 最佳 | 默认。多语种 + 中文方言鲁棒，托管在 GitHub Releases |
| sherpa-onnx-paraformer-zh-2024-03-09 | 228 MB | 高 | 备选（纯中文 Paraformer） |
| openai-whisper / faster-whisper | 142 MB+ | 中 | 备选；模型在 HuggingFace Hub，可能被沙箱代理屏蔽 |

### 推荐参数

```python
sherpa_onnx.OfflineRecognizer.from_sense_voice(
    model="model.int8.onnx",
    tokens="tokens.txt",
    num_threads=4,
    use_itn=True,        # 阿拉伯数字 / 标点逆文本归一
    language="zh",
    debug=False,
)
```

### 音频解码（不依赖系统 ffmpeg）

```python
import av, numpy as np
container = av.open("xxx.mp3")
stream = container.streams.audio[0]
rs = av.AudioResampler(format="flt", layout="mono", rate=16000)
chunks = []
for f in container.decode(stream):
    for of in rs.resample(f):
        chunks.append(of.to_ndarray().flatten())
flushed = rs.resample(None) or []
for of in flushed:
    chunks.append(of.to_ndarray().flatten())
samples = np.concatenate(chunks).astype(np.float32)
```

`av`（PyAV）pip wheel 自带 ffmpeg 动态库，**不需要**预先 `brew install ffmpeg`。

### Dual-mono 检测

很多电话录音 mp3 标记为 stereo，但两声道完全相同。先验证：

```python
if pcm.ndim == 2 and pcm.shape[0] == 2:
    if np.allclose(pcm[0], pcm[1], atol=1e-4):
        print("dual-mono, 跳过声道分离")
```

不要在 dual-mono 上做声道分离。

## 4. 多窗口扫描参数

经验值（已在 8 kHz 电话录音验证）：

```python
HALFS   = (1.5, 2.0, 2.5, 3.0, 3.5)        # 半窗长度（秒）
OFFSETS = (-0.5, 0.0, 0.5)                 # 中心偏移
ENHANCEMENTS = (
    "raw",                                 # 原始
    "bandpass(200-3400Hz)+peak_norm",      # 模拟电话频带 + 归一化
    "gain×3",                              # +9.5 dB
    "gain×3 + bandpass",                   # 二者叠加
)
```

每个客户原声中心点 = `5 × 3 × 4 = 60` 个 ASR 假设；**字面命中**（即有候选直接出现真实地名字面）的概率显著高于单次识别。

## 5. Troubleshooting

### 5.1 macOS arch mismatch

错误：

```
mach-o file, but is an incompatible architecture
(have 'x86_64', need 'arm64')
```

原因：旧版 pip（21.x 自带）选不到 Apple Silicon 的 wheel。

修复：

```bash
pip3 install --user --upgrade pip
pip3 install --user --force-reinstall --no-cache-dir \
    numpy ctranslate2 av torch tokenizers onnxruntime huggingface_hub
```

### 5.2 HuggingFace 403 Forbidden

```
403 Forbidden ... huggingface.co/...
```

原因：沙箱代理常常未把 `huggingface.co` 加入 allowlist。

修复：**改用 GitHub Releases 上的模型**（本 skill 默认 sherpa-onnx + SenseVoice 即是这种）。常见 allowlist 包含 `github.com / objects.githubusercontent.com / release-assets.githubusercontent.com`。

### 5.3 macOS Speech.framework 授权状态 1 (.denied)

如果尝试用 macOS 自带 SFSpeechRecognizer，沙箱内（`CURSOR_SANDBOX=seatbelt`）会直接 .denied 不弹授权框。**不要走 macOS Speech 路线**，按本 skill 用离线 ASR。

### 5.4 PyAV duration 报错

```
TypeError: unsupported format string passed to Fraction.__format__
```

修复：先转为 float：

```python
dur_s = float(stream.duration * stream.time_base) if stream.duration else -1.0
```

## 6. 已知的真实校核样本

来自 examples.md 第 1 条（与本 skill 同一对话生成）：

| 录音字面 | 校核后 | 触发的方音规则 |
|---|---|---|
| 五都镇 | 武都镇 | 完全同音（武 wǔ ↔ 五 wǔ） |
| 云凤村 | 永放村 | ong↔ün（永 yǒng→云 yún）+ ang↔eng（放 fàng→凤 fèng） |
| 4 组 / 四组 | 4 组 | 平翘舌（zǔ↔zhǔ） |
| 二十九号 | 29 号 | use_itn=True 下自动转阿拉伯数字 |

继续校核新案例时，把对应方音规则补到本文件 §1，并把"误 → 对"补到 [examples.md](examples.md) 的"高频错别字字典"。

## 7. 文本端校核规则 R1–R9 详解

下面规则按优先级从高到低应用，是 `batch_verify.py` 引擎与"模型逐条校核"共同遵循的规范。每条都给出可在样本里验证的对照。

### R1. 省份字段规范化

- 简称补"省"后缀：`广西` → `广西省`；其他自治区/直辖市同理（`内蒙古` → `内蒙古自治区`、`北京` → `北京市` 等）。
- 若省份字段误填了县/县级市/县级行政区，跳到 R2 处理。
- 详细地址里若已写明完整省名，则以详细地址为准回填省份。

### R2. 市级字段：县级市/县错填为地级市

把"县级市/县/县级区"上提为对应的**地级市**。已知样本映射：

| 误填（市字段） | 正确地级市 | 该县级行政区在地址里如何处理 |
|---|---|---|
| 江油市（四川） | 绵阳市 | 详细地址需保留并补"江油市" |
| 陇西县（甘肃） | 定西市 | 详细地址需保留"陇西县" |
| 宁都县（江西） | 赣州市 | 详细地址需保留"宁都县" |
| 吴川市（广东） | 湛江市 | 详细地址需保留"吴川市" |

通用做法：当采集到的"市"实际是县/县级市时，
1. 查找其上级地级市，回填到 `校核市`；
2. 在 `校核后地址` 中保留该县级单位（不要丢失）。

### R3. 同音 / 形近错别字纠正

样本中已观察到的常见错位：

| 错 | 对 | 来源 |
|---|---|---|
| 海南省 东风市 | 海南省 东方市 | 市字段错别字 |
| 五都镇 | 武都镇 | 同音 |
| 云凤村 | 永放村 | 形近 |
| 浦口村 | 埠口村 | 同音 / 形近 |
| 每年御景湾小区 | 浚县御金湾小区 | 同音 + 漏县名 |
| 红石镇（滕州） | 洪绪镇 | 同音 |
| 红桥镇（陇西） | 巩昌镇 | 同音 / 误记 |
| 王坡江大安小区 | 黄坡镇 | 同音 + 冗余 |

判定原则：仅当**模型能通过常识或地名库确认**时才纠正；不确定时保留原文并标 `需人工复核`。

### R4. 删除无关备注、说明、掩码

- 删除"收货时直接联系 X 快递员""快递备注""请打电话"等寄送说明。
- 删除掩码符号：`**`、`××`、`XX区` 等占位文字。**若被删除的是关键行政区（如"**区"），需结合地址其他线索补全（见 R5）**。

### R5. 补全缺失的县/区

样本里多次出现详细地址缺县/区的情况：

- `南宁市 英华路一品尊府` → `南宁市 青秀区 英华路一品尊府`（POI 反查，需地图 API）
- `常州市 工人新村` → `常州市 天宁区 工人新村`（POI 反查，需地图 API）
- `江西省 宁都县洛口镇` → `江西省 赣州市 宁都县洛口镇`（R2 自动：县名 → 地级市）
- `山东省泰安市旧县乡王屋店三村` → `山东省泰安市东平县旧县乡王屋店三村`（R5 自动：镇/乡 → 县）
- `陕西省铜川市五里镇西村8号` → `陕西省铜川市宜君县五里镇西村8号`
- `江西省上饶市罗桥街道办龙层` → `江西省上饶市广信区罗桥街道办龙层`

`batch_verify.py` 的 R5 自动补全策略（仅在 `data/pcas-code.json` 存在时启用）：

1. 当地级市字段已知（是合法 prefecture）但 addr 主体里**没有合法的区/县**；
2. 在 addr 主体里收集所有候选 `X{镇,街道,乡}` token（前缀 2–5 字 + 后缀，**按短→长尝试**）；
3. 跨 county/区/市/旗 边界的伪 token 自动剔除（如"青春县狮子镇"不会被当成一个镇名查询，而是落回"狮子镇"）；
4. `divisions.lookup_township(name, hint_prefecture=city, hint_province=prov)` 反查所属 county；
5. 候选**唯一**时自动插入 county（在地级市之后）并标 R5；候选 >1 时仅记 `R5 候选>1: [...]`，提示人工复核。
6. **R5 命中后自动清理伪 county**：扫 addr 主体里形如 `X{县/市/区/旗}` 的 token，对每个 token：
   - 等于 city / R5 找到的 area / prov / 是合法 `is_county()` 或 `is_prefecture()` → 保留
   - 否则视为用户错填的伪 county，自动移除并 note `R5: 清理伪 county [...] (与正解 X 矛盾)`
   - 典型场景：
     - `河北省邯郸市武安县河南店镇` → R5 通过河南店镇反查到涉县，**清理"武安县"** → `河北省邯郸市涉县河南店镇`
     - `湖北省黄冈市青春县狮子镇松树林村` → R5 反查到蕲春县，**清理"青春县"** → `湖北省黄冈市蕲春县狮子镇松树林村`

注意：
- `detect_county_in_addr()` 会先剥离 prov + prefecture（自治州/地级市），再从开头匹配 X{县,市,区,旗}；fallback 搜索窗口 20 字，能正确处理"湖北省恩施土家族苗族自治州利川市…"这类三段式。
- 历史撤县设区/设市的地名通过 `TYPO_DICT` 别名对齐：`济阳县→济阳区(2018)` / `横县→横州市(2021)` 等。

补全前应通过常识 / 地名库验证；不确定的标 `需人工复核`。

### R3b. 镇/街道级 phonetic 兜底

继 R3（县级 phonetic）后的镇/街道级补丁。当 R2 已锁定 target_county、但 addr 主体里的 X{镇/街道/乡} 在该 county 辖镇库里不存在时，从辖镇里找唯一近似候选自动纠正。

典型场景：

- `海南省万宁市万盛镇红山村委会` → `海南省万宁市万城镇红山村委会`（**万宁市无万盛镇，仅万城镇**，shèng/chéng 平翘舌+前后鼻音同时混淆，phon=0.67）
- `广西桂林市新安县新安镇` → `广西桂林市兴安县兴安镇`（兴安/新安 phon=1.00 完全同音）
- `江苏镇江润州区江桥街道` → `江苏镇江润州区蒋乔街道`（jiāng qiáo / jiǎng qiáo，平翘舌不分）
- `三都镇镇头村` → `三都镇头村`（用户多打了"镇"字，后缀叠字 trim）

防护策略（避免误改真实重名地名）：

| 防护 | 触发 | 行为 |
|---|---|---|
| ① 子序列保护 | candidate 是 token 的子序列（如"李庄镇" 是 "李庄古镇" 的子序列）| 跳过，保留 token |
| ② 字符共享保护 | candidate ⊃ token 且 addr 中 token 之前已有 candidate 多出的字（如"禹城**市**中街道"中"市"已存在）| 跳过，不替换以免重复 |
| ③ 词典存在性保护 | 非完全同音(score<1.0) 且 token 在全国镇库里合法存在 | 跳过，用户可能是 hint 填错 |
| ④ 后缀叠字 trim | "X镇镇/X乡乡/X街道街道" | 自动 trim 一份后缀 |

参数：min_score=0.65, 唯一性 gap=0.05（第二名差距 ≥0.05 才算唯一）。命中分级：`phon=1.00` 高置信免复核；`0.75` 中置信；`0.65–0.74` 低置信建议复核。

### R6. 规范化冗余 / 重复门牌号

采集端常出现因输入法或重复点击造成的数字串重复：

- `27栋2222单元44402402房` → `27栋2单元402房`
- `11号11号楼11层西户` → `1号楼11层`

启发式：把连续重复的相同数字段（≥4 位）按其在标准门牌中的合理长度截断（单元号 1–2 位、楼号 1–4 位、房号 3–4 位）。模糊时优先保留较短、符合"X栋X单元X室/房"语义的形式。

### R7. 详细地址回写省/市

当 `校核省` / `校核市` 已修正后，详细地址必须包含修正后的省 + 市前缀：

- 例：原 `四川省江油市五都镇云凤村4组29号` → `四川省绵阳市江油市武都镇永放村4组29号`（绵阳市 + 江油市同时出现）。
- 例：`江西省宁都县洛口镇` → `江西省赣州市宁都县洛口镇`。

### R8. 高德 geocode 反查（在线，可选）

当本地规则 R1–R7 + 镇/街道反查 R5 都兜不住时（仅含 POI/路名/小区、明显错字 X 区/县 phonetic 也救不回），可调用高德 Web API 反查。需要 key：

- 申请地址 https://lbs.amap.com/ → 注册 → 创建应用 → 添加 Key（服务平台选 **Web 服务**）
- 配额：免费 5000 次/天 + 3 QPS
- 用法：`export AMAP_KEY=xxx` 或 `--amap-key xxx`

策略：`--amap-scope empty` 只对"校核区县"空的行（最省额度）；`review` 空+需复核；`all`（默认）全部行可交叉验证。

R8 决策（实测在 175 行真实样本上工作良好）：

| 场景 | 行为 |
|---|---|
| 本地"校核区县"为空 + amap 返回 district | 自动填 amap.district，标 R8 |
| 本地有 + amap 别名/全称差异（"印江县" vs "印江土家族苗族自治县"） | `aliases_of_county` 容差，视为一致 |
| 本地有 ✓ 在地址字面 + amap 给的 ✗ 不在地址字面 | **本地保留**，amap 被 POI 关键词带偏；仅 note |
| amap 给的 ✓ 在地址字面 + 本地 ✗ 不在地址字面 | 自动用 amap 修正 |
| 两者都在 / 都不在地址字面 | 上 review，让人工判 |

> 不一致裁决以"是否在地址字面里出现"为主要依据。结果缓存到 `data/amap_cache.json`，二次运行 0 额度消耗。v10 起 main 先用 `AmapClient.prefetch()` 批量预热（单次 10 个地址走 `batch=true`），5000 行 `--amap-scope empty` 通常仅需 ~13 秒。

### R9. 录音重听 + 自动学习字典（在线，依赖音频脚本）

R8 之后还有 `review_needed=True`（或 `--audio-revisit gaps` 加上 区县/市空）的行，自动**双路 ASR 证据合并**抽取合法地址 token，消除人工复核 + 沉淀学习字典：

1. **双路证据合并**：路径① `--transcript-col` 列里的 ME: 段（上游 ASR）；路径② 本地 `sherpa-onnx SenseVoice int8` 重转写。两路拼接后一起抽 token。
2. **多候选枚举抽 token**：每个 `县/市/区/旗` 后缀位置枚举 2-5 字所有前缀。
3. **typo_translator 透传**：R9 抽到的 token 先走 `PROV_TYPO_DICT` + `TYPO_DICT` + `learned_typo.json` 翻译再 `lookup_county`，能救历史撤县/改区。
4. **多候选裁决**：转写抽到 ≥2 个不同合法 county → 不自动改，写 `R9 多候选: X / Y` 供人工电访裁决。

| 信号 | 行为 |
|---|---|
| 本地"校核区县"为空 + 转写里有 hint_province 内唯一合法 county | 自动填，标 R9 |
| 本地非空但 `lookup_county(local, hint=prov)` 空 + 转写有合法 county | 用转写覆盖；同音对写入 `learned_typo.json` |
| 转写里有 ≥2 个不同合法 county 候选 | 不自动改，保留 review + 备注全部候选 |
| 转写里无任何合法 token（数据真错，如"山西霍山县"） | 保留 review，仅记 note |

**学习字典持久化**：`data/learned_typo.json`，启动时 batch_verify 自动 merge 进 `TYPO_DICT` / `PROV_TYPO_DICT`，第二次跑同分布数据无需再调录音。

CLI：`--audio-revisit review|gaps|learn|all|none`、`--audio-url-col 通话录音`、`--audio-cache-dir /tmp/revisit`、`--audio-timeout 180`。
