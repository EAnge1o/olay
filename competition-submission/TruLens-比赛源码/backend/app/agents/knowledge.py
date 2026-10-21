"""Rule lexicons and scoring weights shared by the text/comment agents.

This is intentionally a data module: the domain knowledge (which phrases signal
fake claims, how much each category should weigh) lives here so it can be
reviewed, extended and eventually replaced by a model without touching logic.
"""

from __future__ import annotations

from app.core.enums import RiskLevel

# ---------------------------------------------------------------------------
# Text analysis
# ---------------------------------------------------------------------------

# 绝对化用语 — regulatory red flags (违反《广告法》绝对化用语禁令)
ABSOLUTE_TERMS: tuple[str, ...] = (
    "100%", "百分之百", "绝对", "永久", "彻底", "根治", "唯一", "第一", "国家级",
    "顶级", "最强", "最有效", "最高级", "史无前例", "无副作用", "零风险",
    "一次见效", "包治", "全网最低", "永不", "万能",
)

# 功效夸大 — promises that no cosmetic can keep
EXAGGERATION_TERMS: tuple[str, ...] = (
    "立竿见影", "瞬间", "一秒", "一秒变白", "7天", "七天", "三天", "马上变白",
    "即刻", "换肤", "逆龄", "冻龄", "如换一张脸", "秒变", "白成一道光",
    "毛孔消失", "永久美白", "从黑变白", "一次白一个度",
)

# 医疗宣称 — cosmetics may not claim medical efficacy
MEDICAL_TERMS: tuple[str, ...] = (
    "治疗", "治愈", "消炎", "杀菌", "祛痘印", "修复敏感肌", "医治", "疗效",
    "医用级", "药妆", "处方", "临床验证", "三甲医院", "皮肤科医生推荐",
    "医生推荐", "医院同款", "可入药", "抗过敏", "激素", "去湿疹",
)

# 虚假专家背书 — invoking authority without a verifiable source
EXPERT_ENTITIES: tuple[str, ...] = (
    "专家推荐", "专家团", "教授推荐", "博士推荐", "医生推荐", "主任推荐",
    "权威机构认证", "国际实验室", "诺贝尔", "科研所", "药监局认证", "临床实验证明",
    "明星同款", "大牌同源", "专柜同款",
)

# 广告披露缺失 — required disclosure terms
AD_DISCLOSURE_TERMS: tuple[str, ...] = (
    "广告", "推广", "合作", "赞助", "品牌方", "佣金", "带货", "商务", "#ad", "#广告",
)

# AI 生成文案特征 — template / lack-of-personal-experience markers
AI_TEMPLATE_MARKERS: tuple[str, ...] = (
    "首先", "其次", "最后", "综上所述", "总而言之", "总的来说", "接下来",
    "不仅", "而且", "无论是", "还是", "堪称", "值得一提",
)

# 真实个人体验特征 — the opposite of AI templating
PERSONAL_EXPERIENCE_MARKERS: tuple[str, ...] = (
    "我用", "我买", "我的", "亲测", "自用", "回购", "空瓶", "踩雷", "我是",
    "专柜", "小样", "我试了", "第几天", "回购了", "已用", "我的肤质",
)

# Category -> (weight, severity, finding type, recommendation)
TEXT_CATEGORY_RULES: dict[str, dict] = {
    "medical_claim": {
        "terms": MEDICAL_TERMS,
        "weight": 32.0,
        "severity": RiskLevel.HIGH,
        "type": "medical_claim",
        "recommendation": "删除医疗/疗效类表述：化妆品不得宣称治疗功效（违反《化妆品监督管理条例》）。",
    },
    "absolute_claim": {
        "terms": ABSOLUTE_TERMS,
        "weight": 24.0,
        "severity": RiskLevel.HIGH,
        "type": "absolute_claim",
        "recommendation": "移除绝对化用语（如“100%”“最”），改用可验证的量化描述。",
    },
    "exaggeration": {
        "terms": EXAGGERATION_TERMS,
        "weight": 20.0,
        "severity": RiskLevel.MEDIUM,
        "type": "exaggeration",
        "recommendation": "收敛功效承诺，补充使用周期、适用肤质等边界条件。",
    },
    "fake_expert": {
        "terms": EXPERT_ENTITIES,
        "weight": 22.0,
        "severity": RiskLevel.HIGH,
        "type": "fake_expert",
        "recommendation": "专家/机构背书需提供可核验来源，否则删除相关表述。",
    },
}

# ---------------------------------------------------------------------------
# Comment analysis
# ---------------------------------------------------------------------------

# 低质量泛化评论 — the bread and butter of paid comment farms
GENERIC_COMMENT_TERMS: tuple[str, ...] = (
    "好用", "不错", "好评", "赞", "喜欢", "买它", "推荐", "已下单", "果断入手",
    "无限回购", "闭眼入", "值得拥有", "五星好评", "好用推荐", "太好用了",
    "真的好用", "回购", "好评如潮", "种草了", "爱了爱了",
)

# Burst detection window
BURST_WINDOW_SECONDS = 60
BURST_MIN_COMMENTS = 5
