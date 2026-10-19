# 图像鉴伪模型实验记录

**状态：实验阶段，阈值与模型组合尚未定版。** 本记录更新时间：2026-10-16。

## 当前实验接口：LOTA、NFA-ViT 独立

1. LOTA 对整张图产生 AI-generation score。
2. 官方 loader 中自然图标签为 `1`，AI 图标签为 `0`。模型输出是 real-class logit；报告中的 AI-generation score 用等价且数值更稳定的 `sigmoid(-logit)` 计算。
3. LOTA 与 NFA-ViT 各自独立调用，没有分数门槛或 gate。
4. NFA-ViT 返回局部候选区域、bbox 和红色 overlay。它提供“哪里值得看”的定位线索，不提供“LOTA 为什么判断为 AI”的因果解释，也不证明被标记区域发生篡改。

LOTA score 是未校准模型分数，不应在 UI 或报告中写成 AI 图像概率。官方论文报告 GenImage 上的高准确率，但训练/测试设定不是小红书美妆图，不能据此推定本项目的误报率。

## 权重与预处理核对（2026-10-16）

- LOTA 官方代码：<https://github.com/hongsong-wang/LOTA>
- LOTA 官方论文：<https://openaccess.thecvf.com/content/ICCV2025/papers/Wang_LOTA_Bit-Planes_Guided_AI-Generated_Image_Detection_ICCV_2025_paper.pdf>
- 当前 checkpoint 存放于 `models/LOTA/Network_best.pth`，大小 94,361,483 字节，SHA-256 为 `66892fc25915043dc784de1182381b9d543e5bea05c54b3e4367db4027f7c5f9`。用户提供的文件名是 `sdv5_scaling_patch32.pth`；复制后的 state_dict 可用 `strict=True` 加载。权重文件本身没有可独立验证训练来源的签名元数据。
- 根据来源文件名及官方配置，当前权重按 Stable Diffusion 1.5 / scaling / patch32 记录；LOTA 官方训练说明是在 GenImage 的一个子集上训练并跨子集评估。模型输出仍为未校准信号。
- 输入预处理语义与上游一致：低 3 bit scaling、32×32 候选块、64 个随机候选中选最大多方向梯度块、缩放到 256×256、ImageNet mean/std 归一化。
- 实现差异：adapter 使用 Pillow bilinear，上游 `bit_patch` 使用 OpenCV `INTER_LINEAR`；上游整批评测只在开始时设全局 seed，adapter 每次单图调用重置 seed 42。当前 API 为了重复调用可复现而固定单图 seed；成对变换实验会为同一基图固定相同裁剪候选位置。
- 已在 VCT² COCO_AI 通用图像上完成 50 张 LOTA 推理；控制变换实验另运行 350 次。结果见下方实验链接。当前没有逐图标注的美妆 AIGC 测试集，不能选择美妆场景正式阈值。

LOTA 与 NFA-ViT 继续独立输出，不互相 gate。`quick_start.py` 的图像 JSON 可检查 `images[*].image_analysis.ai_generation_signal`、`nfa_status` 和 `localized_overlay_path`。是否设置分流阈值，要等带图像级真值的美妆数据评估后决定。

### LOTA 缩放与 JPEG 敏感性实验

使用现有 VCT² 的 50 张成对真实/生成图，比较原图、75% 缩小再放大、JPEG q95/q85/q70/q50，以及缩放后 JPEG q85。75% 缩小再放大使真实组和生成组分数都集中到接近 1；JPEG 单独重编码的 AUC 变化非单调。该结果支持 LOTA 对重采样/低位平面变化敏感，但不能证明小红书实际处理造成同样变化。

- [实验报告](data/model-experiments/lota_degradation_vct50_20261016/report.md)
- [逐图分数](data/model-experiments/lota_degradation_vct50_20261016/results.jsonl)
- [模型与指标汇总](data/model-experiments/lota_degradation_vct50_20261016/summary.json)
- 复跑：`.venv/bin/python scripts/run_lota_degradation_experiment.py --device cpu`

## FerretNet 独立候选

**状态：已加入本机实验入口，未取得权重、未运行推理，尚未与 LOTA 比较或定版。** 这是一项模型替换实验：FerretNet 单独给图片打分，不接在 LOTA 后面，也不触发 NFA-ViT。

- 官方实现：[xigua7105/FerretNet](https://github.com/xigua7105/FerretNet)，Apache-2.0；官方论文发表于 NeurIPS 2025。
- 官方 `FerretNet-B-Median-3` 配置约 1.1M 参数，官方论文报告在其开放生成器基准上的准确率；这些数字不是本项目在美妆图片上的结果。
- 单独实验脚本使用官方 `Test.yaml` 的 256×256 中心裁切和归一化；输出 `score` 是模型的 synthetic-class sigmoid 分数，不是校准概率。
- 官方权重目前通过官方 README 指向的百度网盘提供。还没有下载到本机；需要用户手动下载权重。不要把网页/错误提示文件改名成 `.pth`。
- 上游演示脚本硬编码 `.cuda()`；本模块直接加载官方网络和 LPD 定义，提供 `auto`、MPS、CPU 和 CUDA 设备选择。MPS 兼容性仍需在有权重后验证；自动模式遇到 MPS 推理异常会回退 CPU。

安装并准备官方源代码与权重：

~~~bash
python -m pip install -e ".[ferretnet_local]"
mkdir -p models
git clone --depth 1 https://github.com/xigua7105/FerretNet.git models/FerretNet
~~~

从官方 README 的模型链接下载 `ferretnet-b-median-3.pth`，放到：

~~~text
models/FerretNet/checkpoints/4cls_ckpt/ferretnet-b-median-3.pth
~~~

也可在 `.env` 用 `FERRETNET_REPO_PATH`、`FERRETNET_WEIGHTS_PATH`、`FERRETNET_DEVICE` 指向现有目录/文件。单独给图片或文件夹评分：

~~~bash
python scripts/run_image_detector.py --model ferretnet path/to/image-or-folder --output data/model-experiments/ferretnet.json
~~~

当前已有 LOTA checkpoint，可用同一个 runner 单独运行 `--model lota` 或 `--model ferretnet`，不融合分数。后续比较仍需带真实/生成图像级标签的美妆图，重点看误报、漏报和速度。现有 `quick_start.py` 独立运行 LOTA、NFA-ViT，以及配置启用时的 TruFor；原始分数不做融合。

## ImageTrust-AI 评估

评估对象是 [ImageTrust 官方仓库](https://github.com/CS-Research-Group-UVT/imagetrust)，不是同名文档平台。仓库自述使用 ResNet-50、EfficientNet-B0、ViT-B/16 特征融合，并使用 XGBoost/MLP 和不确定性校准；首次启动约下载 2 GB 权重。完整复现实验还要求约 30 GB embeddings 和 16 GB 显存 GPU；其 14.3 ms/图速度是 RTX 5080、batch=256 上的报告数据。

它的 README 自报社交平台压缩鲁棒性和测试指标，但其跨生成器表也显示多个未见生成器的 TPR 低于 5%（包括 DDPM、BigGAN、GLIDE、Latent Diffusion、VQ-Diffusion），并承认跨生成器泛化仍未解决、对艺术域未验证。上述指标尚未由本项目复现，也没有美妆图评估。因此：

- **目前不把 ImageTrust 集成进主链路。** 它的依赖和模型体积对本机 Hackathon 工作流偏重，且其公开局限正好影响 AIGC 泛化。
- 这轮做的是仓库/论文评估，**没有在本机测试 ImageTrust 推理**，也不把仓库自报指标当作本项目实验结果。
- 若之后需要比较，先用小规模带标签美妆样本离线评估其误报率，再决定是否值得下载完整模型。

## Detective SAM 局部篡改定位实验

**状态：已在官方小样例上跑通；实验用途，不接入生产评分或模型融合。** Detective SAM 面向扩散编辑后的局部篡改定位，输出像素掩码；它不能代替 LOTA/FerretNet 判断整张图是否由 AI 生成。官方论文称其为基于 SAM2 的图像伪造定位模型；本项目结果仅来自两个随仓库提供的 CocoGlide 演示样本，不能视为论文复现或美妆域验证。

- 官方实现：[Gertlek/DetectiveSAM](https://github.com/Gertlek/DetectiveSAM)；权重包标记的授权为 `other`，本地保留用于实验，不随项目提交或再分发。
- 本机只取了默认 Detective SAM checkpoint（约 297 MB）和 SAM2.1 Hiera Base+ backbone（约 324 MB），没有下载完整 923 MB bundle 或额外数据集。`models/` 已被 `.gitignore` 排除。
- 官方依赖要求 PyTorch `>=2.5.1,<2.8`；项目 `.venv` 的 Torch 版本不兼容，因此本次在 `/private/tmp/ct-detectivesam-venv` 临时隔离环境运行（Torch 2.7.1、SAM2 1.1.0）。当前环境没有可用 MPS/CUDA，使用 CPU。
- 两个带 ground-truth mask 的 CocoGlide 样例结果：`banana_28809` IoU 0.8567 / F1 0.9228；`train_221213` IoU 0.5473 / F1 0.7074；均值 IoU 0.7020 / F1 0.8151。样本量只有 2，且来自官方演示集，指标只验证调用与可视化链路。
- 输出、逐样本指标和图像见 [`data/model-experiments/detective_sam_20261015/cocoglide_cpu/`](data/model-experiments/detective_sam_20261015/cocoglide_cpu/)。

本机复跑命令：

~~~bash
cd models/DetectiveSAM
OMP_NUM_THREADS=4 /private/tmp/ct-detectivesam-venv/bin/python -m detectivesam_inference.evaluate \
  --checkpoint detective_sam \
  --dataset-root demo/cocoglide \
  --output-dir ../../data/model-experiments/detective_sam_20261015/cocoglide_cpu \
  --device cpu \
  --max-samples 2 \
  --num-visualizations 2
~~~

它适合作为“局部改图定位/解释”候选。对于没有原图或真实篡改掩码的完整 AIGC 图片，当前 50 张 VCT² 检测集不适合计算定位指标；若拿它输出热力图，应标注为模型定位信号，不能写成确认篡改证据。

## TruFor vs Detective SAM 同集比较

**状态：实验性小样本比较，不接入生产链路。** 两者在相同的两个官方 CoCoGlide 演示样本和真值 mask 上运行，CPU、定位阈值 0.5。Detective SAM 的输入是 target 加内部生成的扰动流；TruFor 输入 target。可视化中的 source 仅供人查看，不传给模型。

| 模型 | 平均 IoU | 平均 F1 | 平均 precision | 平均 recall | 每图推理耗时 |
|---|---:|---:|---:|---:|---:|
| TruFor | 0.601 | 0.725 | 0.930 | 0.663 | 592 ms |
| Detective SAM | **0.702** | **0.815** | 0.928 | **0.758** | 1,187 ms |

Detective SAM 在这两个样本的定位 IoU/F1 更高；TruFor 的 CPU 单图推理约快一倍，但在 `train_221213` 上漏掉了较多标注区域。TruFor 的整图 detector score (`0.537`、`0.359`) 是未校准原始分数，不是造假概率。两个样本都含局部篡改，无法估计真实图片误报率或有效的整图分类表现。样本只有 2 张且不是美妆图，不能据此比较泛化能力。

- 逐图指标、输入说明和局限：[实验报告](data/model-experiments/trufor_detectivesam_cocoglide_20261015/README.md)
- 原始数值：[summary.json](data/model-experiments/trufor_detectivesam_cocoglide_20261015/summary.json)
- mask、定位图、置信度图和对照可视化位于同目录的 `maps/`、`visualizations/`。
- TruFor 官方权重按其仓库说明限信息及非营利用途；正式产品使用前需核实授权。

## TruFor vs Detective SAM：测试数据包（3）

**状态：已完成 10 条帖子、43 张图的探索性推理；没有单图篡改标签或像素 mask，不作为定位 benchmark，也不接入生产评分。** 数据包将前 5 条帖子标为真实、后 5 条标为伪造，但伪造标签覆盖 AI 文案、官方宣传素材、促销详情图和合成试色图等整帖风险，并不代表帖子里的每张图都被篡改。

| 帖子级聚合信号 | 真实组均值 | 伪造组均值 | AUC（n=10） |
|---|---:|---:|---:|
| TruFor 图片检测分数均值 | 0.482 | 0.587 | 0.68 |
| TruFor 图片检测分数最大值 | 0.705 | 0.697 | 0.52 |
| Detective SAM 定位图 top 1% 信号均值 | 0.971 | 0.958 | 0.40 |
| Detective SAM 定位图 top 1% 信号最大值 | 0.997 | 0.990 | 0.32 |

TruFor 的均值信号只显示弱排序，最大值接近随机；Detective SAM 的真实组信号更高，且两组 top 1% 信号普遍接近饱和。不能据此把热图解释为篡改区域或用分数判定内容真假。CPU 平均每图 TruFor 40.72 秒、Detective SAM 1.21 秒；TruFor 用原始 1170×1560/1440×1920 分辨率，Detective SAM 输入为 512×512，耗时并非等像素条件对比。

- [完整实验报告、逐帖分数和可视化](data/model-experiments/trufor_detectivesam_test_package_3_20261015/README.md)
- [全部逐图结果](data/model-experiments/trufor_detectivesam_test_package_3_20261015/summary.json)
- [复跑脚本](scripts/run_trufor_detectivesam_unlabeled.py)

## 固定分流策略前需要的实验

准备真实种草图、AI 全图、AI 局部修改/修图、拼图/截图，以及平台压缩版本，并按图像来源保留标签。至少比较：

- 真实图误报率，尤其是高磨皮、强滤镜、商品海报、文字密集图；
- AI 全图和局部改图的召回率；
- LOTA、NFA-ViT、TruFor 的分数分布、AUROC/AUPRC 和目标 FPR 下的召回；
- NFA-ViT/TruFor 定位图与独立标注 mask 的 IoU/F1、可靠性和误标区域比例；
- 每图耗时与完整 batch 耗时。

运行 `scripts/evaluate_image_scores.py` 可对带标签、group-disjoint 的分数 JSONL 计算校准 split 阈值、test false accusation rate、coverage、AUROC/AUPRC 与 Brier/ECE 诊断。样本不足时保留“实验信号 + 人工复核”，不把模型信号改写成真实性结论。BeautyTrust-300 的字段和采集计划见 `content_trust/evaluation/BEAUTYTRUST_DATASET.md`；真实标注数据尚未提供，模型阈值仍未验证。

## 小红书样本上的 LOTA 头部微调试验

**状态：负面结果；隔离实验，不接入主链路。** 使用 6 张明确披露 AI 的图片（一篇笔记）和按笔记组划分的用户“倾向真实”样本，只微调 LOTA 最后一层分类头。AI 视觉弱标签 `xhs-039` 留作独立探针。微调后留出的 7 张真实候选图分数中位数从约 0 升至 0.740；以未验证的 0.5 观察线计，4/7 越线。`xhs-039` 分数为 0.439，低于 4 张留出真实候选图。该实验没有证明泛化提升，权重不得用于主链路。

NFA-ViT 与 TruFor 未微调：现有数据没有篡改区域 mask 或原始/编辑配对，图像级 AI 来源标签无法正确监督其局部定位任务。

- [实验说明与结果](data/model-experiments/lota_beauty_finetune_20261016/README.md)
- [逐图结果与完整划分](data/model-experiments/lota_beauty_finetune_20261016/report.json)
- [独立实验权重](data/model-experiments/lota_beauty_finetune_20261016/lota_beauty_head_experimental.pth)
- 复跑：`.venv/bin/python scripts/finetune_lota_beauty_head.py`
