# TruLens Agent 真实评估说明

## 1. 评估目标

使用 `TruLens_TestDataset` 的 22 条样本，通过当前正在运行的 TruLens FastAPI 服务做黑盒评估，不修改后端代码、模型调用、提示词、评分逻辑或阈值。

评估覆盖：

- 18 条伪造样本，目标风险等级为 `medium` 或 `high`；
- 4 条对照样本，目标风险等级为 `low`；
- 四维分数、总分、风险等级、交叉验证摘要和耗时；
- 如时间允许，按 `labels.json` 统计疑点级命中。

## 2. Agent 入口

当前实际使用以下 API：

1. `POST /api/upload`
   - `text`：正文
   - `comments`：评论 JSON 数组
   - `account_history`：结构化账号历史 JSON
   - `image`：单张图片
   - `source=eval`
   - `agents=["account","image","comment","text"]`
   - `auto_analyze=true`
2. `GET /api/status/{task_id}`
   - 轮询 `pending / running / completed / failed`
3. `GET /api/report/{task_id}`
   - 获取总分、风险等级、四维分数、证据、交叉验证、决策建议和置信度
4. `GET /api/engine-trace/{task_id}`
   - 获取角色运行、证据链、主要证据、限制和模型元数据

API 根地址默认：`http://127.0.0.1:8000`。

## 3. 当前接口能力与输入映射

| 数据包材料 | 映射方式 |
|---|---|
| `post/content.txt` | `text` |
| `post/image_*.jpg` | 当前接口只支持一张图；按编号排序后上传 `image_1` |
| `comments.txt` | 解析为评论对象后传入 `comments` |
| `reviews.txt` | 与评论合并，传入 `comments` |
| `complaint.txt` | 当前无独立接口字段，附加到 `text` 末尾 |
| `account.txt` | 解析昵称、简介、粉丝数、笔记总览和可解析的历史帖子，传入 `account_history` |
| `comment_images/*.jpg` | 当前接口没有多图/评论附图字段，不拼接、不修改原图，也不上传；在 raw 结果中记录为未提交材料 |

### 单图限制

当前后端 `POST /api/upload` 的图片字段是单个 `UploadFile`，`ContentSample` 也只保存一个 `image_path`。评估不会把多张图拼成一张图，因为这会改变原始输入并可能影响检测结果。

因此：

- 每个样本只提交按文件名编号排序的第一张正文图；
- 正文其余图片和 `comment_images` 不会被送入模型；
- 引用未提交图片的疑点在第二档统计中应标记为未命中，除非 Agent 通过文字材料独立指出了同一现象；
- 这一限制会降低图像类疑点的召回率，但统计结果如实反映当前可运行产品接口。

## 4. 账号解析规则

`account.txt` 只做结构化，不添加模型提示：

- `昵称：` → `handle`
- `简介：` → `profile_text`
- `粉丝：` → `follower_count`
- `近 30 天笔记（共 N 篇）` 或 `近 30 天笔记 N 篇` → `total_post_count`
- `[序号] MM-DD 标题=... | 自述肤质=...` → 历史帖子

无法解析为历史帖子的账号文本只保留可解析字段，不向正文添加额外提示。

## 5. 判定规则

项目报告中的风险等级为：

- `high` = 高风险
- `medium` = 中风险
- `low` = 低风险

第一档判定：

- 伪造样本：`risk_level in {medium, high}` → 判对；
- 对照样本：`risk_level == low` → 判对；
- 其他结果 → 判错或误报；
- 任务失败、报告缺失、轮询超时均记为失败，不补数。

## 6. 失败与重试

- 每个样本最多执行 2 次尝试（原始尝试 + 1 次重试）；
- 网络错误、任务 `failed`、轮询超时或报告读取失败才触发重试；
- 两次都失败时写入失败原因；
- 所有尝试的 upload、状态轮询、最终 report 和 engine trace 都保存在 raw JSON 中。

## 7. 输出

- `eval/results.csv`：样本级第一档统计；
- `eval/raw/sample_0XX.json`：完整 API 返回；
- `eval/label_hits.csv`：疑点级自动候选与人工确认；
- `eval/summary.md`：汇总、失败样本、好/差案例和 PPT 结论；
- `eval/run_eval.py`：唯一评估脚本。

## 8. 约束

- 不修改 `backend/`、`content-trust-engine-v10/` 或 `frontend/`；
- 不修改测试数据；
- 不针对失败样本补提示；
- 不挑选样本；
- 不把泛泛风险描述算作具体疑点命中；
- 不把未提交的多图材料算作已分析材料。
