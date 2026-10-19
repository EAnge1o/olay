# TruLens — 后端 API 契约

> 面向前端同学的联调契约文档。**本文所有示例均为实际运行代码的真实输出**（版本 `0.1.0`），
> 未编造任何字段。若发现文档与实际响应不符，以 `GET /openapi.json` 为准并反馈后端。

- **服务地址（本地）**：`http://127.0.0.1:8000`
- **API 前缀**：`/api`（可通过环境变量 `API_PREFIX` 修改，**不要硬编码以外的假设**）
- **交互式文档**：`GET /docs`（Swagger UI）、`GET /openapi.json`
- **全部路径**：`/`、`/api/health`、`/api/agents`、`/api/upload`、`/api/samples/{sample_id}`、
  `/api/samples/{sample_id}/image`、`/api/samples/{sample_id}/trace`、`/api/analyze/{task_id}`、
  `/api/status/{task_id}`、`/api/report/{task_id}`、`/api/engine-trace/{task_id}`、
  `/api/team/queue`、`/api/team/queue/{task_id}`、`/api/creator/check`
- **前端接入**：见第 4.2 节（`src/api.ts` + `src/LiveAnalysis.tsx`，Vite 代理 `/api`）

---

## 1. 通用约定

### 1.1 维度词汇（**最重要的对齐点**）

产品/前端用 **T / R / U / S** 四个维度描述内容可信度。后端内部用同一个英文 key 表示，
且该 key **就是分析引擎（`content-trust-engine-v10`）的角色名**，两端一字不差，因此
**没有翻译表、没有映射歧义**：

| 前端 code | 后端 key（＝引擎角色） | 前端 label | 该维度回答的问题 | 需要的输入 |
|---|---|---|---|---|
| `T` | `account` | 账号可信度 | 这个账号长期可靠吗？ | 账号主页 / 历史发布 |
| `R` | `image` | 图片真实性 | 这张图是真的吗？ | 图片 |
| `U` | `comment` | 评论区真实性 | 评论是真人说的吗？ | 评论 |
| `S` | `text` | 文案真实性 | 这段话是体验，还是写好的脚本？ | 文案 |

- `GET /api/agents` 的 `dimensions[]` 直接返回上表的 `key` / `code` / `label` / `question` / `input`。
- `GET /api/status` 的 `agents[].code` 与 `GET /api/report` 的 `dimensions[].code` 也都是 `T/R/U/S`，
  **顺序恒为 T → R → U → S**。
- 请求里传 `agents` 子集时，**用后端 key**（`account` / `image` / `comment` / `text`），
  不要传 `T/R/U/S`。

> 历史包袱：早期版本第三个维度叫 `vision`，现已统一为 `image`（与引擎角色名一致）。
> 若看到 `vision`，说明请求打到了旧版本。

### 1.2 错误响应格式（所有错误统一）

任何 4xx/5xx 都返回同一信封：

```json
{
  "error": {
    "code": "not_found",
    "message": "report not ready yet",
    "details": { "task_id": "7fbd15ff...", "status": "pending" }
  }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `error.code` | string | 机器可读错误码，**前端应按 code 分支，不要按 message 文本判断** |
| `error.message` | string | 人类可读描述，可能随版本变化 |
| `error.details` | object | 结构化补充信息，可能为 `{}` |

| `error.code` | HTTP | 触发场景 | 前端建议处理 |
|---|---|---|---|
| `validation_error` | 422 | 业务校验失败（无有效内容、agent 名非法、账号历史格式非法） | 读取 `message`/`details` 提示用户 |
| `request_validation_error` | 422 | 请求体格式非法（如给 multipart 接口发了 JSON） | 检查 `details.errors`，修正请求格式 |
| `not_found` | 404 | task/sample 不存在；或报告尚未生成 | 先查 `/api/status` 的 `report_ready` 再取报告 |
| `conflict` | 409 | 对正在运行的任务重复发起分析 | 忽略并继续轮询 `/api/status` |
| `unsupported_media_type` | 415 | 图片类型不在白名单 / 超过大小上限 | 提示用户更换图片 |
| `external_service_error` | 502 | 外部模型（LLM）调用失败 | 可重试；或关闭 `use_llm` |
| `internal_error` | 500 | 未预期的服务端错误 | 展示兜底错误，保留 `task_id` 反馈后端 |

### 1.3 时间格式

所有时间戳均为 **带时区的 ISO 8601 UTC** 字符串，例如：

```
"2026-10-17T11:59:43.261904+00:00"
```

> **重要**：早期版本返回不带时区后缀的裸字符串，浏览器会按本地时区解析导致显示偏移 8 小时。
> 现已统一带 `+00:00`，前端可直接 `new Date(value)` 后按本地时区渲染。

### 1.4 ID 格式

`task_id` / `sample_id` / 报告 `id` 均为 **32 位十六进制字符串**（无连字符），例如
`1516c99fe09b4f6ca2636096697336ec`。可安全用作 URL 路径参数。

### 1.5 枚举值

| 枚举 | 取值 | 出现位置 |
|---|---|---|
| 任务状态 `status` | `pending` / `running` / `completed` / `failed` | upload、status、analyze |
| 维度 key `agent` | `account` / `image` / `comment` / `text`（`report` 是融合阶段，不可请求） | agents、status、analyze |
| 维度 code `code` | `T` / `R` / `U` / `S` | agents、status、report |
| 风险等级 `risk_level` | `low` / `medium` / `high` | status、report |
| 可信等级 `trust_level` | `high` / `medium` / `low`（`high` 最好） | report |
| 可信标签 `trust_label` / `trust_level` | `高可信` / `存疑` / `低可信` | report、samples |
| 证据来源 `evidence[].source` | `account` / `image` / `comment` / `text` | report |
| 证据严重度 `severity` | `low` / `medium` / `high` | report |
| 评估状态 `engine_status`（内部，见 `assessed`） | `assessed` / `not_applicable` / `insufficient_data` / `unavailable` | 不直接暴露，用 `assessed` 判断 |

### 1.6 分值语义（**易错点，务必读完**）

系统内部一切分数都是 **风险分**（0 = 干净，100 = 最危险）。但**前端展示的是「可信度」**，
因此 API 边界做了显式区分，**同一个响应里不会出现含义模糊的裸 `score` 字段**：

| 字段 | 方向 | 说明 |
|---|---|---|
| `overall_score`（report） | 风险，越高越危险 | 综合风险分 0–100 |
| `trust_score`（report / status / dimensions） | **可信，越高越好** | `100 - 风险分`，前端「Trust Score / 100」用这个 |
| `risk_score`（status / dimensions） | 风险，越高越危险 | 内部口径，供审计与调试 |

`risk_level` / `trust_level` 由后端**自己给定**，前端只读，**不要用分数自己换算等级**。
阈值取决于当前分析后端，用 `/api/health` 的 `analysis_backend` 判断：

| 后端 | `low` | `medium` | `high` |
|---|---|---|---|
| `heuristics`（内置规则，**默认**） | `x < 35` | `35 ≤ x < 70` | `x ≥ 70` |
| `trust_engine`（模型同学的引擎，需 `TRUST_ENGINE_ENABLED=true`） | `x < 30` | `30 ≤ x < 60` | `x ≥ 60` |

**「暂不评分」是一个正常状态，不是 0 分。** 某个维度没有对应输入（没给图片、没给账号历史、评论条数不足…），
该维度的 `assessed=false` 且 `trust_score` / `risk_score` / `risk_level` / `trust_label` **全为 `null`**。
前端必须渲染成「暂不评分」，**绝不能当成 0 分**（那会被误读为「极低可信」）。判定依据：
`/api/status` 的 `agents[].assessed` 与 `/api/report` 的 `dimensions[].assessed`。

**融合规则**：只有 `assessed=true` 的维度参与加权（权重 `text .35 / image .30 / comment .20 / account .15`，
缺失维度按比例归一化）。这样「没检查到的维度」既不会拉高也不会拉低结论。
如果**所有**维度都无法评估，报告会给出中性占位分（`overall_score=50`、`risk_level=medium`）并在
`summary` 中明确说明，同时 `assessed_dimensions` 为空数组 —— 前端此时应整体显示「暂不评分」。

---

## 2. 任务状态机

```
（上传）→ pending → running → completed
                            ↘ failed
```

- `pending`：已创建，分析未开始（或 `auto_analyze=false`）。
- `running`：流水线执行中，`progress` 递增。
- `completed`：结束，可用 `/api/report` 取报告，`report_ready=true`。
- `failed`：流水线异常，`error_message` 有值，**不会产生报告**（取报告返回 404）。

**`progress` 只保证单调不减、范围 0–100，前端不要把具体数值当作契约。**
实际观测序列：`pending=0` → `running` 依次 `5 → 10 → 23 → 36 → 49 → 62 → 90 → 100`。

### 各维度的 `status`（`/api/status` 的 `agents[]`）

- 任务为 `pending` 时，`agents` 为 **空数组**（维度行尚未创建）。
- 流水线启动后依次出现 `pending` → `completed` / `failed`。
- 单个维度失败**不会**让整体任务失败（只要还有至少一个维度成功）；
  该维度的 `status="failed"` 且 `error_message` 有值，其余分数字段为 `null`。
- **`status="completed"` 不代表有分数**：还要看 `assessed`。例如未上传图片时，
  `R` 的 `status="completed"` 但 `assessed=false`、`trust_score=null`（即「暂不评分」）。

---

## 3. 接口详情

### 3.1 `GET /api/health`

健康检查，无参数。

**200 响应**

```json
{
  "status": "ok",
  "version": "0.1.0",
  "environment": "development",
  "database": "sqlite",
  "llm_provider": "mock",
  "llm_enabled": false,
  "analysis_backend": "heuristics"
}
```

| 字段 | 说明 |
|---|---|
| `status` | 固定 `"ok"` |
| `database` | 当前方言：`sqlite`（开发） / `postgresql`（生产） |
| `llm_provider` | 配置的 provider 名，默认 `mock` |
| `llm_enabled` | **服务端总开关**。为 `false` 时请求里的 `use_llm` 不生效（见 3.3） |
| `analysis_backend` | `heuristics`（内置规则）或 `trust_engine`（模型引擎）。**为 `trust_engine` 时 `/api/engine-trace` 才有数据** |
| `xhs_enabled` | 当前是否可抓取小红书链接（`XHS_ENABLED=true` 且运行器存在）。为 `false` 时 `url` 字段会被 422 拒绝 |

---

### 3.2 `GET /api/agents`

列出可用维度与流水线顺序。无参数。

**200 响应**

```json
{
  "pipeline": ["account", "image", "comment", "text", "report"],
  "dimensions": [
    {
      "name": "account",
      "display_name": "账号分析 Agent",
      "description": "账号可信度：发布节奏、带货占比与历史内容一致性",
      "llm_enabled": false,
      "code": "T",
      "label": "账号可信度",
      "question": "这个账号长期可靠吗？",
      "input": "账号主页"
    },
    { "name": "image",   "code": "R", "label": "图片真实性",   "input": "图片",   "question": "这张图是真的吗？", "...": "..." },
    { "name": "comment", "code": "U", "label": "评论区真实性", "input": "评论",   "question": "评论是真人说的吗？", "...": "..." },
    { "name": "text",    "code": "S", "label": "文案真实性",   "input": "文案",   "question": "这段话是体验，还是写好的脚本？", "...": "..." }
  ]
}
```

> 响应键名是 **`dimensions`**（不是 `agents`）。`pipeline` 含 5 个名字，但 `dimensions`
> 只列出 4 个**可请求的分析维度**；`report` 是融合阶段，不作为可请求维度。
> 上述 4 条按 T → R → U → S 顺序返回。

---

### 3.3 `POST /api/upload`

上传内容并创建分析任务。返回 `task_id`，后续所有查询都基于它。

- **Content-Type 必须是 `multipart/form-data`**。给本接口发 `application/json` 会得到 422
  `request_validation_error`。
- 上传时 **`status` 恒为 `"pending"`**（即使 `auto_analyze=true`，分析在响应发出后异步执行）。

**请求字段（全部为 multipart 表单字段）**

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `text` | string | 否* | — | 种草文案（S 维度）。会 `strip()` 后入库 |
| `comments` | string | 否* | — | 评论数据（U 维度）。**JSON 数组字符串**，或换行分隔的纯文本 |
| `account_history` | string | 否* | — | 账号历史（T 维度）。**JSON 对象**，见下方说明 |
| `url` | string | 否* | — | **小红书笔记链接或整段分享文案**（详见 3.3.1）；抓取在后台异步执行 |
| `account_url` | string | 否 | — | 可选的小红书账号主页链接。留空时抓取器会尝试从笔记作者自动发现主页；需与 `url` 一起使用 |
| `image` | file | 否* | — | 图片（R 维度）。`content_type` 须在 `image/jpeg,image/png,image/webp` 内 |
| `source` | string | 否 | `manual` | 来源标识（如 `xiaohongshu`、`douyin_note`） |
| `agents` | string | 否 | 全部 4 个 | 要运行的维度。`"text,comment"` 或 `'["text","comment"]'`（**用后端 key**） |
| `use_llm` | bool | 否 | `false` | 是否允许模型复核（需服务端 `llm_enabled=true` 才生效） |
| `auto_analyze` | bool | 否 | `true` | 上传后立即异步启动分析 |

\* `text` / `comments` / `account_history` / `url` / `image` **至少提供一个有效内容**，否则 422 `validation_error`。
判定基于「有效内容」：纯空白文案、空的 `comments` 数组、空的 `account_history` 都不算有效内容。

**`account_history` 格式**（T 维度）

```json
{
  "handle": "cutie_beauty88",
  "profile_text": "每天一支好物分享 / 合作请私信",
  "follower_count": 8200,
  "total_post_count": 460,
  "posts": [
    { "text": "闭眼入，链接在橱窗，领券下单更划算。", "published_at": "2026-09-28T09:00:00Z", "like_count": 21 },
    { "text": "又回购了，商品卡直接拍，品牌方合作款。",     "published_at": "2026-09-28T09:12:00Z", "like_count": 18 },
    { "text": "无脑入就对了，旗舰店同款。",               "published_at": "2026-09-28T09:26:00Z", "like_count": 15 }
  ]
}
```

- 也接受 **只传一个数组**（等价于 `{"posts": [...]}`），数组元素也可以是纯字符串。
- 每条 post 支持别名：`published_at`/`created_at`、`like_count`/`likes`、`comment_count`/`comments`、
  `source_url`/`url`。
- 后端会为每条 post 补一个稳定唯一 `id`（`post-1`、`post-2`…，重复时自动去重），并对齐键集合。
- **少于 3 篇时 T 维度不评分**（`assessed=false`，summary 会说明「不足 3 篇」）——
  样本太少时筛查发布节奏/同质化没有统计意义，后端不会硬猜。

`comments` 支持的三种写法：

```
"comments":   '[{"content":"好用","author":"u1","likes":3,"created_at":"2026-10-01T10:00:00Z"}]'
"comments":   '["好用","不错"]'
"comments":   "好用\n不错\n回购"
```

**201 响应**

```json
{
  "task_id": "1516c99fe09b4f6ca2636096697336ec",
  "sample_id": "4e9b931576184a8c9771197cae27d0ea",
  "status": "pending",
  "comment_count": 2,
  "text_length": 56,
  "has_image": true,
  "account_post_count": 3,
  "source_url": null
}
```

| 字段 | 说明 |
|---|---|
| `task_id` | 分析任务 ID，用于 status/report |
| `sample_id` | 内容样本 ID，用于 `/api/samples/{sample_id}` 取回原文与图片 |
| `status` | 响应时刻的状态，**恒为 `pending`** |
| `comment_count` | 归一化后的评论条数 |
| `text_length` | 文案长度（已去首尾空白）；传链接时为 0（正文在后台抓取后回填） |
| `has_image` | 是否保存了图片（传链接时为 `false`，抓到的封面图在后台回填） |
| `account_post_count` | 已入库的账号历史篇数（0 表示未传该维度） |
| `source_url` | 传链接时回显**规范化后的笔记 URL**；否则 `null` |

**示例（curl）**

```bash
curl -X POST http://127.0.0.1:8000/api/upload \
  -F 'text=姐妹们！这支精华100%修复敏感肌，7天白一个度，皮肤科医生推荐。' \
  -F 'comments=[{"author":"u1","content":"好用","created_at":"2026-10-01T10:00:00Z"}]' \
  -F 'account_history={"handle":"cutie_beauty88","posts":[{"text":"闭眼入，链接在橱窗。"},{"text":"又回购了，商品卡直接拍。"},{"text":"无脑入就对了，旗舰店同款。"}]}' \
  -F 'image=@./shot.png;type=image/png' \
  -F 'source=xiaohongshu'
```

**错误**

| 场景 | HTTP | code |
|---|---|---|
| 四个内容字段都无有效内容 | 422 | `validation_error` |
| 用了 JSON 而非 multipart | 422 | `request_validation_error` |
| 图片类型不在白名单 | 415 | `unsupported_media_type`（`details.allowed` 含允许值） |
| 图片超过 `MAX_UPLOAD_SIZE_MB`（默认 10MB） | 415 | `unsupported_media_type` |

---

### 3.3.1 小红书链接抓取（`url` 字段）

后端直接驱动 **团队已有的抓取器**（`olay/xhs/XhsExtractor/Crawler.py`，DrissionPage + 真实 Chrome）：
粘贴链接即可自动取回正文 / 图片 / 评论，无需前端做任何解析。

**调用方式**：`url` 直接传整段分享文案也行，后端会做归一化：

```
https://www.xiaohongshu.com/explore/674a1b2c3d4e5f60718293a4    ✅ 直接用
https://www.xiaohongshu.com/discovery/item/674a…?xsec_token=…   ✅ 改写成 /explore/ 形式
9.9 复制打开小红书【标题】… 674a1b2c3d4e5f60718293a4            ✅ 提取裸 ID
https://xhslink.com/abc123                                      ✅ 跟随短链解析
https://example.com/whatever                                    ❌ 422 无法识别的链接
```

**时序**：`POST /api/upload` **立即返回 201**（只做 URL 校验，不开浏览器）；
真正的抓取在后台任务里执行，期间 `progress` 为 `12 → 20`，之后进入四维分析。
所以前端照旧轮询 `/api/status` 即可，无需为链接单独写流程。

**从一条链接能得到什么**（拿不到的一律如实标「暂不评分」，绝不编造）：

| 维度 | 数据来源 | 说明 |
|---|---|---|
| S 文案 | 笔记标题 + 正文 | ✅ 稳定 |
| R 图片 | 封面图（第 1 张） | ✅ 稳定；图片总数记录在 `sample.raw_metadata.xhs_image_count`，当前只分析封面 |
| U 评论 | 评论区 | ✅ 实测可抽取；评论懒加载，条数随页面而定 |
| T 账号 | 笔记作者 ID → 作者主页 → 最近笔记列表 | ✅ 自动发现主页并抓取最近最多 12 篇；也可用 `account_url` 显式指定主页作为兜底。少于 3 篇时仍按规则暂不评分 |

**两条关键实现细节（都踩过坑，别再改回去）**：

1. **必须保留 `xsec_token`**。分享链接形如
   `…/explore/<id>?xsec_token=…&xsec_source=pc_share`；把 query 剥成裸 `/explore/<id>`
   会让小红书**直接跳回 `/explore` 首页**，笔记详情根本加载不出来。
   现在 `/explore/…` 形式的 URL **原样透传**，其他形式改写时会带上 token。
2. **不要依赖 `#detail-title` / `#detail-desc`**。小红书已改版，这两个节点在新版页面上
   已不存在（实测 `document.querySelector('#detail-title') === false`），团队抓取器因此会抛
   `ElementNotFoundError`，**即使笔记页已经正常渲染**。
   后端因此在同一个浏览器会话里改读页面内嵌的 `window.__INITIAL_STATE__`：
   该对象是 **Vue Proxy，无法直接序列化**，所以在页面内遍历后只回传少量字段的 JSON 字符串。
   团队原有的 DOM 选择器全部保留，作为兜底（评论/作者在实测中正是由它们取到的）。

**实测记录（2026-10-17）**：

```
链接: https://www.xiaohongshu.com/explore/6ab0a8bf000000001203e930?xsec_token=…&xsec_source=pc_feed
抓取: 标题 ✅ | 正文 29 字 ✅ | 封面图 1 张 ✅ | 评论 10 条 ✅ | 作者 帕丁顿熊 ✅
报告: trust_score 93.8 高可信 · R 100.0 / U 100.0 / S 85.0 · T 暂不评分（无历史）
耗时: 约 50 秒（真实浏览器抓取 + 真实模型调用）
```

**前置条件与常见失败**：

- 需要 `XHS_ENABLED=true` + `pip install -r requirements-xhs.txt` + 本机 Chrome；
  否则该字段直接 422（`message` 里写明原因）。
- **小红书可能返回风控页**（标题「安全限制」、`error_code=300011 账号异常`）。
  此时任务失败，`error_message` 为可执行的提示：先在浏览器窗口里**登录一次**
  （设置 `XHS_USER_DATA_PATH` 复用该登录态），或直接用
  `--remote-debugging-port=9222` 启动你已登录的 Chrome 并配置 `XHS_LOCAL_PORT=9222`。
- 抓取失败**不会**用占位内容兜底：任务 `failed`，`error_message` 就是原因。
  前端可据此提示用户改用手填文案/评论/图片。

**验证命令**：

```bash
./.venv/Scripts/python scripts/xhs_smoke_test.py "<笔记链接>"            # 只测抓取
./.venv/Scripts/python scripts/xhs_smoke_test.py "<笔记链接>" --analyze  # 抓取 + 四维报告
```

---

### 3.4 `GET /api/samples/{sample_id}`

取回上传的原始内容，用于「内容详情 / 报告回看」页面。**【新增接口，不属于原始契约】**

**200 响应**（`comments` 仅列 1 条示意）

```json
{
  "id": "4e9b931576184a8c9771197cae27d0ea",
  "text_content": "姐妹们！这支寡肽精华真的绝了！我是敏感肌，用了7天痘印就淡了，毛孔也消失了，皮肤科医生推荐，100%修复敏感肌。",
  "text_length": 56,
  "comment_count": 2,
  "comments": [
    { "index": 0, "content": "好用", "author": "u1", "likes": 0, "created_at": "2026-10-01T10:00:00Z" }
  ],
  "account": {
    "handle": "cutie_beauty88",
    "profile_text": null,
    "follower_count": 8200,
    "total_post_count": 460,
    "post_count": 3,
    "posts": [
      { "id": "post-1", "text": "闭眼入，链接在橱窗，领券下单更划算。", "published_at": "2026-09-28T09:00:00Z", "like_count": 21, "comment_count": 0, "source_url": null }
    ]
  },
  "image": {
    "filename": "shot.png",
    "content_type": "image/png",
    "size": 70,
    "width": 1,
    "height": 1,
    "has_exif": false,
    "sha256": "bf780d1105e7cbb81732af82d45fa3c8a359a130cb5cc6083abdf296e1445e2a",
    "url": "/api/samples/4e9b931576184a8c9771197cae27d0ea/image"
  },
  "source": "doc_example",
  "created_at": "2026-10-17T11:59:43.127000+00:00"
}
```

| 字段 | 说明 |
|---|---|
| `text_content` | 文案原文；未上传则为 `null` |
| `comments[]` | **形状恒定**，每项固定 5 个键：`index`(int)、`content`(str)、`author`(str\|null)、`likes`(int)、`created_at`(str\|null，**原样回显客户端值，未做时区归一**) |
| `account` | 未传 `account_history` 时为 `null`；否则给出 `post_count` 与归一化后的 `posts[]` |
| `image` | 未上传图片时为 `null` |
| `image.width`/`height`/`has_exif` | 图片探测结果；`has_exif=false` 是「AI 生成图」的弱信号 |
| `image.url` | 可直接用于 `<img src>`，相对路径，拼上服务地址即可 |

**错误**：`sample_id` 不存在 → 404 `not_found`。

---

### 3.5 `GET /api/samples/{sample_id}/image`

以流的形式返回图片原文件。**【新增接口，不属于原始契约】**

- **200**：`Content-Type` 为实际图片类型，`Content-Disposition: inline`（`<img>` 可直接渲染，不强制下载）。
- **404** `not_found`：样本不存在 / 该样本无图片 / 磁盘文件缺失。

> 上传后前端本地已持有该文件可直接预览；本接口用于**报告回看**（刷新页面后重新展示图片）。

---

### 3.6 `POST /api/analyze/{task_id}`

启动分析流程（异步）。**上传时 `auto_analyze=true`（默认）已自动触发，通常无需再调用本接口；**仅用于
`auto_analyze=false` 的场景或「重新分析」。

- 请求体 **可选 JSON**，省略或传 `{}` 均可。请求体必须为 JSON（不可用表单）。

**请求体（可选）**

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `agents` | string[] \| null | 全部 4 个 | 要运行的维度子集，如 `["account","text"]`（**后端 key**）；乱序/去重由后端处理并返回规范顺序 |
| `use_llm` | bool | `false` | 是否允许模型复核 (**需服务端 `llm_enabled=true` 才生效**) |

**200 响应**

```json
{
  "task_id": "1516c99fe09b4f6ca2636096697336ec",
  "status": "running",
  "requested_agents": ["account", "image", "comment", "text"],
  "message": "analysis started; poll GET /api/status/{task_id}"
}
```

> 返回 `status: "running"` 仅代表**已受理**。真实执行在响应之后异步进行，
> **前端必须轮询 `/api/status/{task_id}` 直到 `completed`/`failed`**。

**错误**

| 场景 | HTTP | code |
|---|---|---|
| `task_id` 不存在 | 404 | `not_found` |
| 该任务已在运行中 | 409 | `conflict`（`details.progress` 为当前进度） |
| `agents` 含非法名 | 422 | `validation_error`（`details.available` = `["account","image","comment","text"]`） |

**示例**

```bash
curl -X POST http://127.0.0.1:8000/api/analyze/<task_id> \
  -H 'Content-Type: application/json' -d '{"agents":["account","text"]}'
```

---

### 3.7 `GET /api/status/{task_id}`

任务与各维度的运行状态，用于进度条与「四维工作流」展示。

**200 响应（completed，四维全部给出了分数）**

```json
{
  "task_id": "1516c99fe09b4f6ca2636096697336ec",
  "sample_id": "4e9b931576184a8c9771197cae27d0ea",
  "status": "completed",
  "progress": 100,
  "requested_agents": ["account", "image", "comment", "text"],
  "error_message": null,
  "created_at": "2026-10-17T11:59:43.126085+00:00",
  "started_at": "2026-10-17T11:59:43.189739+00:00",
  "finished_at": "2026-10-17T11:59:43.261987+00:00",
  "report_ready": true,
  "agents": [
    { "agent": "account", "code": "T", "label": "账号可信度",   "status": "completed", "assessed": true, "risk_level": "medium", "risk_score": 55.0,  "trust_level": "medium", "trust_score": 45.0, "trust_label": "存疑",   "duration_ms": 0, "error_message": null },
    { "agent": "image",   "code": "R", "label": "图片真实性",   "status": "completed", "assessed": true, "risk_level": "low",    "risk_score": 18.0,  "trust_level": "high",   "trust_score": 82.0, "trust_label": "高可信", "duration_ms": 0, "error_message": null },
    { "agent": "comment", "code": "U", "label": "评论区真实性", "status": "completed", "assessed": true, "risk_level": "high",   "risk_score": 75.0,  "trust_level": "low",    "trust_score": 25.0, "trust_label": "低可信", "duration_ms": 0, "error_message": null },
    { "agent": "text",    "code": "S", "label": "文案真实性",   "status": "completed", "assessed": true, "risk_level": "high",   "risk_score": 100.0, "trust_level": "low",    "trust_score": 0.0,  "trust_label": "低可信", "duration_ms": 0, "error_message": null }
  ]
}
```

**200 响应（未上传图片 → R 维度「暂不评分」）**

```json
{
  "agent": "image", "code": "R", "label": "图片真实性",
  "status": "completed", "assessed": false,
  "risk_level": null, "risk_score": null,
  "trust_level": null, "trust_score": null, "trust_label": null,
  "duration_ms": 0, "error_message": null
}
```

**200 响应（pending，未开始）**

```json
{
  "task_id": "1516c99fe09b4f6ca2636096697336ec",
  "sample_id": "4e9b931576184a8c9771197cae27d0ea",
  "status": "pending",
  "progress": 0,
  "requested_agents": ["account", "image", "comment", "text"],
  "error_message": null,
  "created_at": "2026-10-17T11:59:43.126085+00:00",
  "started_at": null,
  "finished_at": null,
  "report_ready": false,
  "agents": []
}
```

| 字段 | 说明 |
|---|---|
| `status` | 任务状态（见第 2 节） |
| `progress` | 0–100，仅作进度指示 |
| `report_ready` | **为 `true` 时才可调 `/api/report`** |
| `agents[]` | 每项：`agent`(key)、`code`(T/R/U/S)、`label`、`status`、`assessed`、`risk_level`(可 null)、`risk_score`(可 null)、`trust_level`(可 null)、`trust_score`(可 null)、`trust_label`(可 null)、`duration_ms`、`error_message` |
| `agents[].assessed` | **为 `false` 时渲染「暂不评分」**，不要显示 0 |
| `agents[].trust_label` | 与 `report.dimensions[].trust_label` 同源，轮询视图与最终报告口径一致 |
| `error_message` | 任务级失败原因；成功时为 `null` |

> **注意**：本接口**不返回**每个维度的 `findings`/`summary`/`recommendations` 明细。
> 前端「四维工作流」面板用 `agents[].trust_score` + `report.evidence`（按 `source` 过滤）即可实现；
> 逐维度完整明细见 `/api/report` 的 `dimensions[]`。

**错误**：不存在 → 404 `not_found`。

---

### 3.8 `GET /api/report/{task_id}`

最终可信度报告（四维 T/R/U/S）。**仅在 `status.report_ready == true` 时可用。**

**200 响应**（`evidence` 列 2 条、`recommendations` 列 2 条示意；`dimensions[].evidence` 已省略）

```json
{
  "id": "7d5b2a40c0264016aa806a9683b6c560",
  "task_id": "1516c99fe09b4f6ca2636096697336ec",
  "overall_score": 63.6,
  "trust_score": 36.4,
  "risk_level": "medium",
  "trust_level": "medium",
  "trust_label": "存疑",
  "summary": "综合可信度核验结论：中等风险（风险分 63.6/100，可信分 36）。各维度风险分：account 55、image 18、comment 75、text 100。共提取 12 条证据，其中高风险 8 条。",
  "evidence": [
    {
      "source": "comment",
      "type": "duplicate_comment",
      "evidence": "『好用』及其 1 条高度相似评论",
      "confidence": 0.95,
      "severity": "high",
      "reason": "1 组重复/近似重复评论，重复占比 100%。"
    },
    {
      "source": "account",
      "type": "commercial_density",
      "evidence": "3 篇历史内容中 100% 含商品/导流标记",
      "confidence": 0.9,
      "severity": "high",
      "reason": "带货占比偏高，历史内容的独立体验价值有限。"
    }
  ],
  "recommendations": [
    "核实历史内容的商业合作披露比例，避免纯导流内容被当作体验分享。",
    "排查重复评论来源，疑似同一批账号批量复制粘贴。"
  ],
  "dimension_scores": { "account": 55.0, "image": 18.0, "comment": 75.0, "text": 100.0 },
  "dimensions": [
    {
      "key": "account",
      "code": "T",
      "label": "账号可信度",
      "question": "这个账号长期可靠吗？",
      "input_label": "账号主页",
      "assessed": true,
      "trust_score": 45.0,
      "risk_score": 55.0,
      "risk_level": "medium",
      "trust_level": "medium",
      "trust_label": "存疑",
      "summary": "账号『cutie_beauty88』历史内容（3 篇）发现 2 处可疑模式。",
      "evidence": [],
      "limitations": ["账号分析只覆盖调用方提供的历史样本，不代表完整账号画像。"]
    },
    {
      "key": "image",
      "code": "R",
      "label": "图片真实性",
      "question": "这张图是真的吗？",
      "input_label": "图片",
      "assessed": false,
      "trust_score": null,
      "risk_score": null,
      "risk_level": null,
      "trust_level": null,
      "trust_label": null,
      "summary": "未提供图片，图片真实性维度不适用。",
      "evidence": [],
      "limitations": []
    },
    { "key": "comment", "code": "U", "label": "评论区真实性", "assessed": true, "trust_score": 25.0, "risk_score": 75.0, "risk_level": "high", "trust_level": "low", "trust_label": "低可信", "summary": "共 2 条评论，刷评风险分 75.0/100，重复率 100%，泛化好评率 100%。", "evidence": [], "limitations": [] },
    { "key": "text", "code": "S", "label": "文案真实性", "assessed": true, "trust_score": 0.0, "risk_score": 100.0, "risk_level": "high", "trust_level": "low", "trust_label": "低可信", "summary": "文案风险分 100.0/100，共 7 处风险点，主要问题：absolute_claim, exaggeration, fake_expert, medical_claim。", "evidence": [], "limitations": [] }
  ],
  "assessed_dimensions": ["account", "image", "comment", "text"],
  "generated_at": "2026-10-17T11:59:43.261904+00:00",
  "created_at": "2026-10-17T11:59:43.267406+00:00"
}
```

| 字段 | 说明 |
|---|---|
| `overall_score` | 综合**风险分** 0–100（越高越危险） |
| `trust_score` | **可信分** = `100 - overall_score`，前端「Trust Score / 100」用这个 |
| `risk_level` / `trust_level` | 风险等级 / 可信等级（后者是前者的反向映射） |
| `trust_label` | `高可信` / `存疑` / `低可信`，可直接展示为结论 pill |
| `summary` | 一句话结论文本，可直接展示 |
| `evidence[].source` | 证据来自哪个维度：`account`/`image`/`comment`/`text` |
| `evidence[].severity` | 该条证据的严重度，用于排序/配色 |
| `evidence[].confidence` | 0–1 置信度 |
| `evidence[].reason` | 人类可读解释 |
| `dimension_scores` | 各维度**风险分**，键即后端 key；**只包含实际评估过的维度** |
| `dimensions[]` | **四维完整视图，恒为 T→R→U→S 四条**（无论是否评估过） |
| `cross_checks[]` | 后端计算的维度间关系：`conflict/consistent/corroborated/doubtful/unverified` |
| `decision` | 后端分级与消费者/创作者/平台/品牌四类建议 |
| `confidence` | 基于真实已评估维度、证据数和交叉验证项计算的置信度 |
| `dimensions[].trust_score` | 该维度可信分；`assessed=false` 时为 `null` → 渲染「暂不评分」 |
| `dimensions[].limitations` | 该维度自己的局限说明（必须随分数一起展示，避免被读成「造假概率」） |
| `assessed_dimensions` | 真正给出结论的维度 key 列表；**为空数组时前端整体显示「暂不评分」** |

**`evidence[].type` / `dimensions[].evidence[].type` 的取值取决于当前后端**
（见 `/api/health` 的 `analysis_backend`）

`analysis_backend = heuristics`（内置规则，默认）：

| 来源 | `type` 取值 |
|---|---|
| `text` | `medical_claim`、`absolute_claim`、`exaggeration`、`fake_expert`、`ai_generated_text`、`undisclosed_ad` |
| `comment` | `duplicate_comment`、`low_quality_comment`、`burst_activity`、`suspicious_author` |
| `image` | `metadata_stripped`、`possible_ai_generated` |
| `account` | `commercial_density`、`posting_burst`、`repetitive_posts` |

`analysis_backend = trust_engine`（引擎后端，实测值）：

| 来源 | `type` 取值 |
|---|---|
| `text` | `exaggerated_claim`、`unsupported_claim`、`contradicted_claim`、`semantic_anomaly`、`fake_endorsement_risk`、`ai_generated_text` |
| `comment` | 引擎的 `pattern_type`，如 `repeated_template` |
| `image` | 引擎图片 finding 类别，如 `ai_generated_image`、`image_text_mismatch`；图片模型不可用时该来源为空 |
| `account` | 统一归为 `account_pattern`（引擎的账号结论用 `verdict` 而非 `category`，后端已适配） |
| 任意 | `engine_evidence`（引擎只给出人类可读证据、未细分类型时的兜底） |

> **`type` 是开放集合**：两条后端路径取值不同，引擎也会随版本新增类别。
> **前端必须对未知 `type` 做兜底渲染，禁止白名单强校验。**

**错误**

| 场景 | HTTP | code | 处理 |
|---|---|---|---|
| `task_id` 不存在 | 404 | `not_found` | 检查 task_id |
| 任务尚未完成 | 404 | `not_found`，`message="report not ready yet"`，`details.status` 为当前状态 | **继续轮询 `/api/status`** |
| 任务已失败 | 404 | `not_found`，`message="analysis task failed; no report generated"`，`details.error` 为失败原因 | 展示失败原因，提供重试 |

> 「报告未就绪」也返回 404 是**既有稳定契约**（已有测试锁定）。
> **正确姿势是先判断 `/api/status` 的 `report_ready`，不要靠 404 猜状态。**

---

### 3.8.1 `GET /api/samples/{sample_id}/trace`

按图片 `sha256` 在本地已核验内容库中查询完全相同的素材复用。响应包含
`sha256`、`match_count`、`matches[]` 和人类可读 `note`；不会用模糊描述冒充实测匹配。

### 3.8.2 `GET /api/team/queue`

从数据库中的已完成任务和报告实时生成团队队列，响应包含：

- `items[]`：标题、作者、可信分、风险等级、置信度、已评估维度、主要矛盾、角色建议、处理状态。
- `summary`：总量、高风险、待复核及各置信度数量。
- `groups[]`：同账号或同图片 SHA-256 形成的真实关联分组。

### 3.8.3 `PATCH /api/team/queue/{task_id}`

持久化人工处理状态。请求：

```json
{ "status": "pending" }
```

`status` 可取 `pending`、`watching`、`done`。

### 3.8.4 `POST /api/creator/check`

`multipart/form-data` 创作者真实自检：

- `draft`：草稿文案。
- `is_collaboration`：是否为合作或赠品内容。
- `category`：品类。
- `image`：可选首张配图。

接口会实际运行文案 Agent（有图时同时运行图片 Agent），返回 `publication_state`、
真实维度分数、逐条 `issues[]`、披露清单、优点和完整 `report`。

### 3.9 `GET /api/engine-trace/{task_id}`

模型同学交付的分析引擎（`content-trust-engine-v10`）的**完整执行轨迹**。**【新增只读接口，不属于原始契约】**

只在服务端启用引擎（`analysis_backend=trust_engine`）**且引擎本次产出了可用结果**时才有数据；
否则返回 404。用于「四角色工作流 / 证据链 / 模型局限」这类评委向的可解释性展示。

**200 响应**（`evidence_chain` 仅列 1 条示意，实际约 20–40 条）

```json
{
  "id": "1f0a...",
  "task_id": "c23e6132c6f04b44a41bea5030239daf",
  "backend": "trust_engine",
  "engine_schema_version": "2.1",
  "overall_score": 63.27,
  "overall_risk_level": "high",
  "visible_roles": [
    {
      "role": "text",
      "provider": "deepseek",
      "model": "deepseek-flash",
      "prompt_version": "v10",
      "status": "fallback",
      "summary": "DeepSeek 未配置，已使用本地规则；提取 13 条 claim。",
      "error_code": null,
      "evidence_chain_ids": ["text-claim-1", "text-ai-style-signal", "text-finding-endorsement-6"]
    }
  ],
  "dimension_assessments": [
    {
      "dimension": "comment",
      "status": "assessed",
      "score": 20.0,
      "risk_level": "low",
      "configured_weight": 0.2,
      "normalized_weight": 0.363636,
      "summary": "评论维度风险分 20.0/100（低风险）；已覆盖 6 条评论，发现 1 处重复模式。",
      "evidence": ["已覆盖 6 条评论，发现 1 处重复模式。"],
      "limitations": ["评论文本模式只能提示异常，不能证明评论为假、付费或协同。"]
    }
  ],
  "evidence_chain": [
    {
      "id": "text-claim-1",
      "dimension": "text",
      "source_kind": "text",
      "source_id": "post_text",
      "excerpt": "姐妹们！",
      "explanation": "No high-risk factual pattern matched.",
      "start_char": 0,
      "end_char": 4,
      "image_id": null,
      "claim_id": "claim-1",
      "finding_id": null,
      "related_ids": [],
      "bbox": null
    }
  ],
  "major_evidence": ["这支寡肽精华真的绝了！"],
  "limitations": [
    "人工复核优先级是分流建议，不是真假结论或校准概率。",
    "未提供外部证据；事实性 claim 不应据此判为虚假。"
  ],
  "provider_errors": ["provider_not_configured"],
  "model_metadata": { "dimension_weights": { "image": 0.3, "text": 0.35, "comment": 0.2, "account": 0.15 } },
  "evidence_count": 38,
  "latency_ms": 11,
  "created_at": "2026-10-17T11:50:47.000000+00:00"
}
```

| 字段 | 说明 |
|---|---|
| `engine_schema_version` | 引擎报告 schema 版本（当前实测 `2.1`；交付文档写的是 `2.0`，以实际返回为准） |
| `overall_score` / `overall_risk_level` | 引擎自己的加权聚合（权重 image .30 / text .35 / comment .20 / account .15），阈值 30/60 |
| `visible_roles[]` | 四个角色的**可见执行状态**：`role`、`provider`、`model`、`prompt_version`、`status`、`error_code`、`evidence_chain_ids`。**无隐藏思维链**。配置 `GLM_API_KEY` 后，`image` 角色会报 `provider="glm_vision"`、`model` 为 `GLM_MODEL`（引擎内部硬编码的 `qwen_vision` 标签已由后端纠正） |
| `visible_roles[].role` | `account` / `image` / `comment` / `text`（与 API 维度 key 一致） |
| `visible_roles[].status` | `completed` / `fallback`（本地规则） / `partial` / `skipped` / `unavailable` / `insufficient_data` |
| `dimension_assessments[]` | 每个维度的 `status`（`assessed`/`insufficient_data`/`unavailable`）、`score`、`risk_level`、权重与**该维度自己的局限** |
| `evidence_chain[]` | 原子级证据：`excerpt` + `start_char`/`end_char` 字符偏移、`claim_id`/`finding_id`/`image_id` 关联、可选归一化 `bbox` |
| `major_evidence[]` | 引擎挑选的主要依据，可直接展示 |
| `limitations[]` | **引擎明确要求展示的局限**。展示分数时必须一并呈现：分数是内部分流建议，**不是内容为假的概率** |
| `provider_errors[]` | 提供方错误码列表；无凭据时为 `["provider_not_configured"]`（各角色降级为本地规则） |
| `latency_ms` | 引擎本次执行耗时 |

> **展示约束（来自模型同学）**：不要把 `overall_score` / `risk_level` / `review_priority`
> 渲染成「造假概率」或「真伪判定」。请把 `limitations` 一并展示。

**错误**

| 场景 | HTTP | code | 说明 |
|---|---|---|---|
| `task_id` 不存在 | 404 | `not_found` | 检查 task_id |
| 任务由内置启发式完成 | 404 | `not_found`，`message="engine trace not available for this task"` | `details.reason` 有说明；用 `/api/health` 判断后端 |
| 引擎启用但本次无可用维度 | 404 | `not_found` | 同上（引擎不可用时已回退启发式） |

---

### 3.10 `GET /`（服务信息）

```json
{ "name": "TruLens", "version": "0.1.0", "docs": "/docs", "api_prefix": "/api" }
```

---

## 4. 前端联调

### 4.1 推荐时序

```
1. POST /api/upload            (multipart, auto_analyze=true 默认)
      → task_id, sample_id
2. GET  /api/status/{task_id}  轮询（建议 800ms–1.5s 间隔）
      直到 status ∈ {completed, failed}
      期间可用 agents[].status / agents[].assessed / progress 渲染进度
3. GET  /api/report/{task_id}  仅当 status.report_ready == true
4. GET  /api/samples/{sample_id}        展示原文/评论/账号历史/图片元信息（可选）
   GET  /api/samples/{sample_id}/image  展示图片（可选）
5. GET  /api/engine-trace/{task_id}     「四角色工作流 / 证据链 / 模型局限」展示（可选，可能 404）
```

- 「重新分析」：`POST /api/analyze/{task_id}`（可传 `agents` 子集），随后同样轮询。
- 服务端默认 `llm_enabled=false`，此时传 `use_llm=true` 不会报错但也不产生模型复核结果。
- 分析后端由服务端决定（`/api/health` 的 `analysis_backend`）。前端**不需要**为两条后端写两套 UI：
  第 1–4 步两条路径完全一致；只有第 5 步（引擎轨迹）在启发式后端下必然 404，属正常分支。

### 4.2 前端已接入部分（`fronted/`）

前端**主流程已接真实后端**（上传 → 轮询 → 报告），共有三层：

| 文件 | 作用 |
|---|---|
| `fronted/src/api.ts` | 类型化 fetch 客户端：`uploadContent` / `analyzeTask` / `getStatus` / `getReport` / `getSample` / `getEngineTrace` / `waitForReport` / `describeApiError`，以及本文档所有响应/字段的 TS 类型定义 |
| `fronted/src/useLiveAnalysis.ts` | **唯一**的真实分析状态源（`phase/taskId/status/agents/report/sample/error` + `start()`/`reset()`）。上传、轮询、取报告都在这里；各屏只读状态，不重复实现 |
| `fronted/src/LiveReport.tsx` | 共享的真实报告视图 `LiveReportView`（Trust Score、风险带、四维雷达、逐维度分数与证据/局限、证据链、建议），以及轮询用的 `LiveProgressList` |
| `fronted/src/LiveAnalysis.tsx` | 「实时核验」独立页：完整表单（文案/评论/账号历史/图片）→ `useLiveAnalysis` → `LiveReportView`。深链 `/?live=true` |
| `fronted/vite.config.ts` | dev/preview 的 `/api` 代理到 `http://127.0.0.1:8000`（`VITE_API_TARGET` 可覆盖），**前端同源调用，不依赖 CORS** |
| `fronted/src/index.css` | `.live-*` 样式块（实时核验表单、进度态、维度局限说明） |

**注入到主流程的方式**（`App.tsx`）：

- `App` 调一次 `useLiveAnalysis()`，把状态投喂给三个既有屏：
  - `Chat` ← `live`：拿到 `report_ready` 后**提前结束脚本演示动画并自动跳报告**（约 1s 后跳转；若用户从报告返回，则不会再次弹回）；演示动画在无真实数据时照常播放。
  - `AnalysisSteps` ← `liveAgents`：四维评审团的分数/结论改用 `/api/status` 的真实 `trust_score` / `trust_label`；`assessed=false` 的维度显示「—」+「暂不评分」而不显示数字。
  - `Result` ← `liveReport` / `liveSample`：渲染真实报告；**无真实数据时仍走原来的硬编码分支**。
- `Composer` / `Workspace` 新增 `onPickFile`，把真实 `File` 交给 `App`，使图片能真正上传到 R 维度。
- 回滚成本：删掉 `useLiveAnalysis()` 调用与上述 props 即可退回纯静态演示。

`api.ts` 中的字段名与本文档一一对应，**新增字段请同步更新两处**：

```
AgentStatusItem.trust_level / trust_label        ←  /api/status  agents[]
DimensionRead.trust_score / risk_score           ←  /api/report  dimensions[]
ReportRead.assessed_dimensions / trust_label
```

---

## 5. 前端必须遵守的约定（Gotchas）

1. **upload 只能用 `multipart/form-data`**，不能用 JSON。
2. **`comments` / `account_history` 在 multipart 里必须是一段字符串**（JSON 文本或换行文本），
   不能是嵌套数组/对象。浏览器端：`formData.append('comments', JSON.stringify(arr))`。
3. **区分方向**：`overall_score` / `risk_score` 是**风险分**（越高越危险）；
   `trust_score` 是**可信分**（越高越好），「Trust Score / 100」用后者。
4. **`assessed=false` 就是「暂不评分」**，分数字段全为 `null`；**不要当 0 分渲染**。
5. **时间戳已带 `+00:00`**，直接 `new Date()` 即可；不要再手动减 8 小时。
6. **不要用 `/api/report` 的 404 判断「还在跑」**，用 `/api/status.report_ready`。
7. `POST /api/analyze` 返回 `running` **不代表已完成**；必须轮询。
8. `agents[].risk_level` / `trust_score` 在 `assessed=false` 时为 `null`，需判空。
9. `dimension_scores` 的键**只包含实际评估过的维度**，缺维度不要当作 0 处理；
   要「四维齐全」请用 `report.dimensions[]`（恒为 4 条）。
10. 对 `evidence[].type` 做**未知值兜底**，不要白名单强校验（两条后端取值不同，见 3.8）。
11. `progress` 数值不是契约，只做进度指示。
12. **维度 key 用 `account`/`image`/`comment`/`text`**（请求用 key，展示用 `code`）。
13. **风险阈值取决于后端**：`heuristics` 用 35/70，`trust_engine` 用 30/60。
    前端只读 `risk_level`，**不要用分数自行换算等级**。
14. `/api/engine-trace` 在启发式后端下**必然 404**，不要把它当作必需调用。
    展示引擎分数时请**一并展示 `limitations`**，避免被解读成「造假概率」。

---

## 6. 各维度能力边界（供前端预期管理）

1. **T 账号可信度**：只筛查调用方提供的历史样本（发布节奏、带货占比、同质化），
   **不推断账号身份、动机或是否为机器人**；**少于 3 篇历史时不评分**。
2. **R 图片真实性（启发式后端）**：只做**元数据启发式**（EXIF 是否存在、尺寸是否为生成模型默认输出），
   属占位级实现，分数意义有限；要更可靠的判定需接像素级检测模型。
   引擎后端由 **GLM（智谱）** 完成 —— 我们通过引擎的 `crossmodal_provider` 注入点替换掉了它内置的
   Qwen-VL 客户端；**未配置 `GLM_API_KEY`（且无 `QWEN_API_KEY`）时该维度为 `unavailable`、不评分**，
   引擎会如实标注 `provider_not_configured` 而不会伪造结论。
   > **注意 `provider_http_429`**：免费档 GLM 视觉模型限流很紧。短时间内连续核验会触发 429，
   > 该次 R 维度报 `unavailable`（前端显示「暂不评分」，`dimension_scores` 里没有 `image` 键）。
   > 这**不是提取失败**：图片文件已经抓下来并入库（`sample.image_path` 有值、
   > `raw_metadata.analysed_image_count == 1`）。已通过 `GLM_MAX_RETRIES`（默认 3）自动退避重试；
   > 若仍频繁 429，可换非免费档视觉模型，或错开连续核验。
3. **U 评论区真实性**：评论文本模式只能提示异常，**不能证明评论为假、付费或协同**。
4. **S 文案真实性**：基于规则/模型识别夸大、绝对化、医疗宣称、AI 生成痕迹与未披露广告，
   证据不足不等于虚假。
5. **逐维度明细**：`/api/status` 只给状态与分数；`/api/report` 的 `dimensions[]` 给出每维度的
   `summary` / `evidence[]` / `limitations[]`，前端展开证据即可。引擎后端另有
   `/api/engine-trace/{task_id}` 提供四角色状态与完整证据链。
6. **无任务列表/历史接口**：前端目前无法列出历史任务，只能凭已有 `task_id` 查询。
7. **超限图片返回 415 而非 413**：语义上更接近 413，但 415 为既有契约，未改动。
8. **单进程任务守卫**：`POST /api/analyze` 的重复触发防护是**单进程内**的；
   多 worker 部署（`uvicorn --workers > 1`）需改为数据库锁。黑客松演示用单进程即可。
9. **认证**：当前无鉴权，任何来源可调用全部接口（黑客松范围内）。
10. **引擎包是可选依赖**：未安装 `content_trust` 时，即使 `TRUST_ENGINE_ENABLED=true` 也会
    **静默回退**到启发式（服务端日志有 WARNING），`/api/engine-trace` 随之 404。
    前端只需按 `/api/health` 的 `analysis_backend` 分支，无需感知。
11. **引擎无 Key 时的行为**：`DeepSeek` 未配置时 `account` / `text` / `comment` 走引擎本地规则
    （`visible_roles[].status="fallback"`）；`account` 角色在无 Key 时会报 `unavailable`，
    此时 T 维度「暂不评分」——这是引擎的真实能力边界，不是 bug。
12. **引擎交付包自身有 3 个过期断言**（其自测为 `50 passed / 3 failed`）：schema 新增字段、
    crossmodal 未配置时返回信息对象而非 `None`、中文 reason 仍按英文断言。
    属其测试未随实现更新，**不影响我们的接入**；已反馈给模型同学。
13. **GLM 接入的验证状态**（用无效 Key 实测，2026-10-17）：
    - ✅ 端点可达且路径正确：`https://open.bigmodel.cn/api/paas/v4/` 返回
      `401 令牌已过期或验证不正确`，引擎归码为 `provider_http_401`，图片维度按预期降级为
      `unavailable`（其余维度不受影响）。
    - ✅ 注入与标签生效：`visible_roles[image]` 报 `provider="glm_vision"`、
      `model="glm-5.3-flashx"`、`prompt_version="glm_v1"`；`role_provider_map.image` 同步为
      `glm_vision`（引擎内部硬编码的 `qwen_vision` 已纠正）。
    - ⚠️ **请求体是否被接受尚未证实**：鉴权先于 body 校验，因此原始 base64、
      `response_format=json_object`、`max_tokens` 三者只有在**有效 Key** 下才能最终确认。
      拿到 Key 后请跑 `scripts/engine_smoke_test.py`；若报错，排查顺序
      `GLM_IMAGE_URL_FORMAT` → `GLM_MODEL` → `GLM_MAX_TOKENS`。

---

## 7. 环境与部署（前端联调相关）

| 变量 | 默认 | 说明 |
|---|---|---|
| `API_PREFIX` | `/api` | 接口前缀 |
| `CORS_ORIGINS` | `["*"]` | JSON 数组字符串；生产建议改为前端域名 |
| `MAX_UPLOAD_SIZE_MB` | `10` | 图片大小上限 |
| `ALLOWED_IMAGE_TYPES` | `["image/jpeg","image/png","image/webp"]` | 图片 MIME 白名单 |
| `ENABLE_LLM` | `false` | 关闭时 `use_llm` 不生效 |
| `DATABASE_URL` | `sqlite+aiosqlite:///./storage/trulens.db` | 相对路径按**进程工作目录**解析；建议在 `backend/` 下启动 |
| `TRUST_ENGINE_ENABLED` | `false` | 是否启用模型同学的引擎；关闭时走内置启发式 |
| `DEEPSEEK_API_KEY` | 空 | 引擎的文案 / 评论 / 账号 / 说明角色；为空时走引擎本地规则并标记 `fallback` |
| `GLM_API_KEY` | 空 | **图片语义角色的首选提供方（智谱 GLM）**；为空时回退 Qwen，再为空则图片维度 `unavailable` |
| `GLM_BASE_URL` / `GLM_MODEL` | `https://open.bigmodel.cn/api/paas/v4/` / `glm-5.3-flashx` | GLM 兼容端点与模型。**必须是多模态模型**（图片角色会把图本身发过去）；实测 `glm-5.3-flashx` 可正确读图，`glm-4.6v-flash` / `glm-4.5v` 亦可用但更易过载（错误码 1305） |
| `GLM_MAX_TOKENS` / `GLM_IMAGE_URL_FORMAT` | `4096` / `raw` | 输出上限 / 图片编码形态（`raw` 为智谱原生 base64） |
| `GLM_MAX_RETRIES` | `3` | 图片角色的传输层重试。**免费档 GLM 限流很紧（HTTP 429）**；设为 `0` 时一次 429 就会让 R 维度降级为 `unavailable`（表现为「暂不评分」），保留重试会按指数退避并遵循 `Retry-After` |
| `QWEN_API_KEY` | 空 | 引擎内置 Qwen-VL 回退，**仅当 `GLM_API_KEY` 为空时生效** |
| `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` | `https://api.deepseek.com` / `deepseek-flash` | 引擎文案角色端点 |
| `QWEN_VL_BASE_URL` / `QWEN_VL_MODEL` | DashScope 兼容端点 / `qwen3.8-flash` | 引擎图片角色端点 |
| `XHS_ENABLED` | `false` | 是否允许抓取小红书链接（需 Chrome + `requirements-xhs.txt`） |
| `XHS_TIMEOUT_SECONDS` | `240` | 单次抓取超时（浏览器可能停在登录/验证页） |
| `XHS_MAX_COMMENTS` | `80` | 单次最多抽取的评论条数 |
| `XHS_USER_DATA_PATH` | `./storage/chrome-profile` | 持久 Chrome profile，登录一次后复用 |
| `XHS_HEADLESS` | `false` | 是否无头运行（无头无法登录，需已有登录态） |
| `XHS_LOCAL_PORT` | 未设置 | 设置后改为连接该调试端口上已运行的 Chrome（复用你已登录的会话） |
| `VITE_API_TARGET`（前端） | `http://127.0.0.1:8000` | Vite dev/preview 的 `/api` 代理目标 |

本地启动（后端）：

```bash
cd backend
python -m venv .venv && ./.venv/Scripts/pip install -r requirements.txt
./.venv/Scripts/pip install -r requirements-engine.txt        # 引擎运行依赖
./.venv/Scripts/pip install -e ../content-trust-engine-v10    # 引擎包本体（可选）
./.venv/Scripts/python run.py                                 # http://127.0.0.1:8000/docs
```

本地启动（前端，另开一个终端）：

```bash
cd fronted
npm install
npm run dev                       # http://localhost:8443/?live=true
```

验证：

```bash
cd backend
./.venv/Scripts/python -m pytest                       # 契约 + 端到端测试
./.venv/Scripts/python scripts/smoke_test.py           # 启发式后端全链路（四维）
./.venv/Scripts/python scripts/engine_smoke_test.py    # 引擎接管分析维度
```

Docker（**引擎已打进镜像**）：

```bash
cd backend
docker compose up --build        # 端口 8000，默认 target=engine，含引擎包
```

> **构建上下文是父目录**：默认的 `engine` 构建目标会把 `content-trust-engine-v10/`
> 拷进镜像，而 Docker 不能 COPY 上下文之外的文件。因此 `docker-compose.yml` 里
> `build.context: ..`（即 `olay/`，同时包含 `backend/` 与 `content-trust-engine-v10/`），
> `.dockerignore` 也相应放在 `olay/` 根目录。
>
> - 容器内 `TRUST_ENGINE_ENABLED` 默认为 `true`（镜像就是为跑引擎构建的）；
>   `backend/.env` 里的显式设置仍然优先。
> - 只要 `backend/`、没有引擎文件夹时：`BUILD_TARGET=lean docker compose up --build`
>   可构建不含引擎的镜像（启发式照常工作）。
> - Key 从 `backend/.env` 自动注入容器（compose 插值读取 `GLM_API_KEY` / `DEEPSEEK_API_KEY`）。

---

*契约随代码演进。任何接口变更都必须同步更新本文并补充 `tests/test_contract.py` 断言。*
