# Content Trust Engine

独立的 Python 3.11+ 内容信任分析模块。它提供稳定的同步 Python API，检查美妆种草文案、图片可见语义、图文冲突、评论文本模式和可用来源记录。

当前报告 schema 是 `2.0`：不输出单一真假分数。报告把不同来源的 observation、evidence 和 inference 分开，输出 `review_priority`（`low`、`review`、`high`）作为人工分流建议。所有实验图像分数都带 `score_semantics`，不代表 AI 生成或造假的概率。

## 安装和开始

在仓库根目录运行：

~~~bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
~~~

在 `.env` 配置 Qwen 图片分析 key。模型默认使用 `qwen3.8-flash`，默认兼容接口地址已内置：

~~~dotenv
QWEN_API_KEY=你的_阿里云百炼_api_key
~~~

DeepSeek 用于文案 claim 分析、评论模式复核、产品名提取和最终说明；它是可选项，未配置时这些环节使用本地规则或说明降级。若已配置 DeepSeek，可保留：

~~~dotenv
DEEPSEEK_API_KEY=你的_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
~~~

用一段文案调用：

~~~python
from content_trust import EvidenceItem, TrustInput, analyze_content

report = analyze_content(TrustInput(
    content_id="demo-001",
    text="7天淡纹80%，敏感肌绝对不会过敏。",
    evidence_items=[EvidenceItem(
        id="study-001",
        source="产品实验资料",
        text="连续使用4周，平均改善18%。",
    )],
))

print(report.model_dump_json(indent=2))
~~~

也可以复用 engine：

~~~python
from content_trust import ContentTrustEngine, TrustInput

engine = ContentTrustEngine()
report = engine.analyze(TrustInput(content_id="post-002", text="我混油皮，夏天用着清爽。"))
~~~

仓库内的小红书样例可用 `python quick_start.py` 分析。quick start 会逐阶段报告进度，并把完整 JSON 保存到 `data/xhs_69410496000000001f00d4bf_20261014/reports/content-report.json`。如需分析其他内容，可直接使用 `analyze_content()` 或 `analyze_content_batch()`，无需改动 quick start。

仓库中已有的 `data/**/content-report.json` 和 Markdown 报告是 schema 1.x 的历史运行快照，保留用于追溯；它们不会自动迁移。当前集成应以本版本 `TrustReport` schema 2.0 为准，重新运行 quick start 后目标 JSON 会更新为新结构。

## 报告怎么读

- `review_priority` 是人工处理顺序建议，不是真假判定或概率。单个 LOTA、NFA-ViT、TruFor 原始信号和 AI 文本来源信号不会单独提高优先级。
- `findings` 只放需要复核的具体问题。每条 finding 包含 observation、reason、evidence IDs、source 和建议动作。图像模型的原始信号使用 `verdict="unknown"`、`risk_score=null`，分数放 `signal_score`。
- `Claim` 包含 `subject`、`metric`、`value`、`unit`、`duration`、`population`、`method`、`comparator`，并通过 `evidence_alignment` 分别对齐产品/配方、人群、终点、方法、时间、对照和结果。
- 没有相关证据时，事实 claim 是 `insufficient_evidence`，不能因此称为假。一个有时间和数值的功效结果保留为一个可核验的 atomic claim，细节拆入结构化字段，避免产生“7天”和“80%”这种重叠碎片。
- 主观肤感、喜好和个人体验不需要功效证据，不会因为“缺少证据”被判成事实错误。
- `text_analysis.text_integrity` 可引用可疑句段、提供对照片段和字符偏移。它只提供风格/结构线索。单篇最终文本不能证明 AI 生成或被编辑；确认拼接或改写需要原稿、版本历史或可核验来源。
- `evidence_cards` 按来源分别展示 provenance、图像取证、图文语义一致性、claim 证据和评论文本模式。
- 最终 JSON 可用 `report.model_dump(mode="json")` 序列化。

schema 2.0 移除了 `overall_risk_score` 和 `risk_level`。接入旧版本的调用方需要迁移到 `review_priority` 与各维度的证据卡；不要把优先级渲染成造假概率。

## 文案、证据和图文一致性

DeepSeek 文案 Structured JSON 输出由 Pydantic 校验；Qwen 图片输出也经 `CrossModalResult` Pydantic 校验，无效 JSON 最多重试一次。提示词独立版本化，报告 `model_metadata` 记录 claim `v7`、Qwen crossmodal `qwen_v1`、comment `v2` 和 explanation `v8`。

事实核验只使用调用方提供的 `evidence_items`。当前引擎不自动联网搜索；没有相关证据时，事实性 claim 应标记为证据不足，不能据此判假。后续接入中文搜索时，搜索结果仍需保留原始来源 URL 和内容，并优先采用监管机构、品牌官方资料或可核查研究作为证据。

批量帖子正文只做一次 claim 分析，每张图各做一次 VLM 可见内容提取和图文比较，整批只生成一次说明：

~~~python
from content_trust import BatchTrustInput, ContentTrustEngine, TrustImageInput

engine = ContentTrustEngine()
report = engine.analyze_batch(BatchTrustInput(
    content_id="post-003",
    text="图片里是 X Serum。",
    images=[
        TrustImageInput(id="slide-1", image_path="./slide-1.webp"),
        TrustImageInput(id="slide-2", image_url="https://example.test/slide-2.jpg"),
    ],
))
print(report.model_dump_json(indent=2))
~~~

Qwen Vision 按每张图片调用，记录可见包装、人物、场景和可辨认文字，再与帖子文案及已抽取 claims 比较。无法从图片验证的疗效、成分或安全结果返回 `NOT_VERIFIABLE`。Qwen 可报告具体可见的视觉异常作为初筛线索，但不提供经过校准的 AI 生成概率，也不能证明 Photoshop 痕迹、扩散伪影或像素级篡改。

## 图片模型实验

LOTA、NFA-ViT、TruFor 仍保留为独立实验工具，但主引擎默认不会初始化或调用它们；旧的权重路径、Space URL 和 `TRUFOR_ENABLED` 不会改变主运行。主引擎图片分析由 Qwen Vision 完成，不会把旧模型分数融合进来。以下说明只适用于单独运行这些实验脚本或显式注入 provider：

- **LOTA**：全图 AI 生成候选信号。`ai_generation_signal.score` 未在美妆社交图片校准。
- **NFA-ViT**：局部 AI 生成/修补候选区域。其叠加图和外框是模型定位输出，不证明篡改，也不解释 LOTA。
- **TruFor**：可选图像完整性/局部定位信号，包含检测分数、定位图、叠加图和 reliability-map 均值。它不是 AI 全图生成概率，也没有美妆域验证。

如需单独复现实验，可调用本机 NFA-ViT 服务或 Hugging Face Space；Space URL 写入 `NFA_VIT_SPACE_URL`。这些实验配置不会被默认主流程读取。其 Space/模型权重许可与硬件成本需要按部署方式单独核实。

LOTA 本机推理的可选依赖包含 OpenCV，用于与上游 evaluation pipeline 保持一致的 patch resize：

~~~bash
python -m pip install -e ".[lota_local]"
python scripts/audit_lota_preprocessing.py
~~~

审计会比较固定样本的上游/产品预处理 tensor、checkpoint logits 和互补 AI 分数，并将逐阶段记录写入 `data/model-experiments/lota_preprocessing_sanity_20261016/`。

在 Apple Silicon 或 CPU 本机运行 NFA-ViT，不需要 Hugging Face Pro：

~~~bash
python -m pip install -e ".[nfa_vit_local]"
git clone --depth 1 https://github.com/clpbc/BR-Gen.git models/BR-Gen
NFA_VIT_DEVICE=auto python scripts/run_nfa_vit_local.py
~~~

让本机 Gradio 服务保持运行，在另一个终端的 `.env` 设置 `NFA_VIT_SPACE_URL=http://127.0.0.1:7860`，再执行 `python quick_start.py`。第一次启动需要从 Hugging Face 获取约 428 MB 权重；后续推理在本机执行。Apple Silicon 上会尝试 MPS，不可用时回退 CPU。使用私有 Space 时配置 Space URL 和有权访问它的 `HF_TOKEN`。

### 可选本机 TruFor

当前默认关闭。先准备上游源码与已获授权的 checkpoint，再安装：

~~~bash
python -m pip install -e ".[trufor_local,provenance]"
~~~

设置 `.env`：

~~~dotenv
TRUFOR_ENABLED=true
TRUFOR_REPO_PATH=models/TruFor/TruFor_train_test
TRUFOR_CHECKPOINT_PATH=models/TruFor/TruFor_train_test/pretrained_models/trufor.pth.tar
TRUFOR_DEVICE=cpu
~~~

TruFor 只接收本地图片路径；图片 URL 时会优雅返回该 provider 不可用，其他图片/文本分析继续。检测图和红色 overlay 缓存在 `/tmp/content-trust/trufor/`，报告路径位于 `image_analysis.trufor_analysis`。上游实现的许可目前限制商业用途，正式发布前需检查 [TruFor 许可与实现](https://github.com/grip-unina/TruFor)。

## C2PA 和 EXIF

可选安装 `python -m pip install -e ".[provenance]"`。本地图片会读取嵌入 C2PA manifest 的验证状态和少量标准字段，并提取 EXIF 相机、软件和原始拍摄时间。读取时关闭远端 manifest/OCSP 请求。

缺少 C2PA 或 EXIF 是中性状态，不能提高风险；有效签名记录来源声明，也不能证明商品功效或发布者可信。远程图片 URL 暂不下载到本地做 provenance 检查。

## 评论文本模式

传入 `CommentInput` 列表即可：

~~~python
from content_trust import CommentInput, TrustInput, analyze_content

report = analyze_content(TrustInput(
    content_id="post-with-comments",
    text="这款精华日常用着清爽。",
    comments=[
        CommentInput(id="c-1", text="好棒的分享！"),
        CommentInput(id="c-2", text="加我领取优惠，点链接购买"),
    ],
))
~~~

评论先在本地做规范化、完全/近似重复和导流模式聚类，最多把 5 个压缩簇交给一次 DeepSeek 调用。报告中 `total_comment_count` 表示传入条数，`llm_input_count` 表示复核簇数；具体簇和代表文本在 `comment_analysis.pattern_clusters`。短评、正向、相似表达或促销内容都不能证明评论者是机器人、付费用户或真实买家，也不用于账户身份判断。

## 小规模评估

第三方服务单测都用 fake provider，不会消耗 API credits：

~~~bash
python -m pytest
python -m content_trust.evaluation.runner
python -m content_trust.evaluation.runner content_trust/evaluation/text_integrity_cases.jsonl
~~~

通用 JSONL runner 汇总 JSON 有效率、类别命中率、误报 case、priority 分布、provider 失败、延迟和说明完整度。需要 DeepSeek 的 case 在离线模式标成 skipped；`--live` 才会使用 `.env` 的 provider。

### 美妆图像校准数据格式

`content_trust/evaluation/beautytrust_manifest.schema.json` 和 `BEAUTYTRUST_DATASET.md` 定义了 BeautyTrust-300 的采集格式/计划，**仓库当前没有 300 张已标注图片**。不要从模型输出反推真值。需要原图/编辑记录、创作者确认、有效 C2PA、受控生成记录或独立标注共识作为 ground truth。原图与编辑版应分到同一 split；同一 post、creator、source 或 generation pipeline 不能跨 train/calibration/test。

已有检测分数可离线评估，不会调用模型或 API。每行 JSONL 包含 `id`、`target`、`split`、`label`、nullable `score` 和 `group_ids`；可选 `evidence_present` 标注该次输出是否附带可检查的定位图或其他证据载荷。例如：

~~~bash
python scripts/evaluate_image_scores.py path/to/image-scores.jsonl \
  --target ai_generated --target-fpr 0.05
~~~

输出包括 AUROC、AUPRC、原始分数的 Brier/ECE 诊断、evidence coverage、abstention、校准 split 选出的目标 FPR 阈值、test TPR 和 false accusation rate。Brier/ECE 对当前未校准原始分数只是诊断，不能说成概率校准结果；正式概率需要独立校准并在 held-out test 验证。无数据或类别不足时相关指标会是 `null`，不会猜测。

## 配置和故障降级

图片分析只需在 `.env` 设置 `QWEN_API_KEY`；模型默认为 `qwen3.8-flash`，Qwen timeout 默认为 180 秒。`DEEPSEEK_API_KEY` 用于文案、评论、账号历史和最终说明。`DASHSCOPE_API_KEY` 仍作为旧环境变量名兼容。主流程的四个可见角色为：DeepSeek 文案、DeepSeek 评论、DeepSeek 账号历史，以及 Qwen 图片语义/图文一致性；没有相应输入的角色会返回 `skipped` 状态。LOTA、NFA-ViT、TruFor 的配置只供独立实验使用，不会进入主流程。

DeepSeek 文案分析会返回 `ai_text_signal.score`（0–1）和具体风格线索。该值是未校准的 AI 文风疑似度，不是统计学意义上的作者来源概率，不证明文本由 AI 生成，也不参与文案或整体风险分；Pangram 仍未接入。分析只在同一次文案请求中完成，不额外增加 AI 文本检测调用。

每个 provider 单独返回错误/不可用状态。DeepSeek 失败会使用本地文案规则并继续图片/评论其他阶段；图像 provider 失败不会吞掉文本分析；说明模型失败时会用本地整理器生成 evidence-card summary。raw 第三方响应只保存在内部 adapter 结果，不进入最终 JSON。

## AIAgent 维度路由、分数和任务追问

统一入口会根据输入选择 `image`、`text`、`comment`、`account` 维度。单文案只跑 text；图片加文案只跑对应维度；截图可选用 Qwen 识别可见正文、评论、账号资料和区域坐标；传入账号历史时才运行账号历史分析。账号异常只做模式筛查，不推断账号身份或动机，至少需要 3 条可比较的历史帖子。

~~~python
from content_trust import AgentInput, ContentTrustAgent

agent = ContentTrustAgent()
result = agent.analyze(AgentInput(
    task_id="demo-task-1",
    content_id="post-001",
    text="这款精华连续使用 7 天，淡纹 80%。我觉得肤感很清爽。",
    locale="zh-CN",
))

payload = result.model_dump(mode="json")
print(payload["overall_score"], payload["overall_risk_level"])
print(payload["dimension_assessments"])
print(payload["role_runs"])
print(payload["evidence_chain"])
print(payload["analysis_report"]["text_analysis"]["ai_text_signal"])

answer = agent.follow_up(result.task_id, "哪些句子缺少证据？")
print(answer.model_dump(mode="json"))
~~~

统一报告包括已启用维度、每维度 0–100 内部风险分、低/中/高等级、主要依据、`role_runs` 和 `evidence_chain`。`role_runs` 记录四个角色的 provider/model、状态、prompt 版本和关联的证据链 ID；`debug_log` 只记录阶段与错误，不包含隐藏思维链。证据链含原子 claim 原文区间、AI 文风线索、Qwen 图片观察、图片可见文字、评论/账号样本及外部 evidence 引用；截图抽取结果另提供归一化 bbox。只有已完成分析且有分数的维度参与加权总分，其余权重会重新归一化。默认权重为 image 0.30、text 0.35、comment 0.20、account 0.15；阈值为 30（中风险）、60（高风险）。这些分数不是内容或账号为假的概率。

`debug_log` 是可选的运行阶段、耗时、提供方错误码和抽取摘要，不包含模型隐藏思维链，也不会进入 `analysis_report` 的用户说明。`follow_up(task_id, question)` 绑定本进程保存的输入、结构化报告、证据链和运行事件；默认上下文保存在内存，进程重启后消失。需要跨进程持久化时，可以给 `ContentTrustAgent(task_store=...)` 注入一个实现可变映射接口的存储。

截图通过 `AgentInput(screenshots=[AgentScreenshotInput(...)])` 输入；账号历史通过 `AccountHistoryInput(posts=[AccountPostRecord(...)])` 输入。图片、评论和证据继续使用现有的 `TrustImageInput`、`CommentInput` 和 `EvidenceItem`。

帖子 URL 使用可注入的 `PostDataFetcher` adapter。URL 模式会请求四个维度；本模块没有默认的小红书抓取器，因此没有接入有授权的抓取实现时会明确返回 `post_fetcher_not_configured`，不可用维度不会以 0 分参与总分，也不会伪称已抓到帖子数据。截图 OCR 需要 `QWEN_API_KEY`；文本、评论、账号历史和任务追问使用 `DEEPSEEK_API_KEY`。独立分析缺少模型时会保留可用的本地规则结果或返回 unavailable 状态。

## 开发检查

~~~bash
python -m compileall -q content_trust quick_start.py scripts
python -m pytest -q
python -m content_trust.evaluation.runner content_trust/evaluation/text_integrity_cases.jsonl
~~~
