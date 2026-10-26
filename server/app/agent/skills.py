"""内置临床医学 skill（预制判断框架，非计算服务）。

定位（AGENTS.md 第 3 条）：让模型在**判断环节**有稳定的临床思维框架，
但所有数值判定（异常/趋势/阈值）仍由代码与 SQL 决定，skill 只提供
提问清单与鉴别思路，不生成结论性诊断。

每个 skill 是一段结构化提示片段，由 planner 按问题选择注入。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class ClinicalSkill:
    id: str
    title: str
    triggers: tuple[str, ...]
    guidance: str


_SKILLS: List[ClinicalSkill] = [
    ClinicalSkill(
        id="lab_interpretation",
        title="化验单解读",
        triggers=(
            "化验", "检验", "血常规", "血生化", "肝功", "肾功", "血脂", "血糖",
            "指标", "参考范围", "偏高", "偏低",
        ),
        guidance=(
            "解读化验结果时按以下顺序：\n"
            "1) 先说明该指标的标准名称、所属类别与参考区间来源（报告自带 vs 标准区间）。\n"
            "2) 区分「轻度偏离」与「显著异常」，并说明是否需要复查；不得仅凭单次数值下诊断。\n"
            "3) 主动指出影响结果的前置条件：空腹与否、采样时间、运动、药物、月经周期、"
            "标本溶血等。\n"
            "4) 对成对指标（收缩压/舒张压、MCV/MCH/MCHC、总胆固醇/甘油三酯、"
            "HDL/LDL、中性粒/淋巴）必须分别说明，不得合并解释。\n"
            "5) 若档案中缺少对照基线，明确说明「依据有限」，并给出下一步该补的检查或信息。"
        ),
    ),
    ClinicalSkill(
        id="symptom_triage",
        title="症状分诊与危险信号",
        triggers=(
            "疼", "痛", "晕", "发热", "发烧", "咳嗽", "胸闷", "心慌", "气短",
            "恶心", "呕吐", "腹泻", "出血", "麻木", "抽搐", "意识",
        ),
        guidance=(
            "处理症状类提问时：\n"
            "1) 先判断是否存在需立即就医的危险信号（胸痛伴出汗/放射痛、突发偏瘫或言语不清、"
            "意识改变、呼吸困难、持续高热、剧烈头痛、大量出血、抽搐）。\n"
            "2) 明确区分「建议尽快就医」与「可先观察并记录」，说明各自的判断依据。\n"
            "3) 给出可执行的自我观察项（体温、血压、症状持续时间、诱因、缓解因素）。\n"
            "4) 不得给出具体处方剂量；可说明常见处理原则但不替代医生面诊。\n"
            "5) 对老年人、孕妇、儿童、有基础疾病者提高就医建议的权重。"
        ),
    ),
    ClinicalSkill(
        id="medication_safety",
        title="用药安全",
        triggers=(
            "药", "服用", "剂量", "副作用", "不良反应", "配伍", "禁忌", "停药",
            "抗生素", "降压药", "降糖药", "他汀",
        ),
        guidance=(
            "处理用药问题时：\n"
            "1) 先确认药名（通用名优先）、剂量、频次、开始时间与适应证。\n"
            "2) 关注相互作用与禁忌：肝肾功能不全、孕期、老年多药共用、"
            "与酒精/葡萄柚等食物相互作用。\n"
            "3) 说明常见不良反应的识别信号与需要立即停药就医的情形。\n"
            "4) 明确提示：调整剂量或停药必须由开具处方的医生决定，不得自行更改。\n"
            "5) 若档案中没有用药记录，先询问而非假设。"
        ),
    ),
    ClinicalSkill(
        id="chronic_followup",
        title="慢病随访",
        triggers=(
            "高血压", "糖尿病", "血脂", "随访", "复查", "控制目标", "达标",
            "糖化", "血压监测", "长期",
        ),
        guidance=(
            "处理慢病随访问题时：\n"
            "1) 用档案中的历史数据说明趋势（变化方向、幅度、波动），趋势由系统计算，"
            "不要自行推算。\n"
            "2) 说明当前控制状态是否达标，并给出参考目标区间及其适用人群差异。\n"
            "3) 给出下次复查建议的项目与合理时间窗，说明理由。\n"
            "4) 识别需要升级治疗的信号（如持续不达标、出现靶器官损害迹象）。\n"
            "5) 强调生活方式干预的证据等级，避免绝对化表述。"
        ),
    ),
    ClinicalSkill(
        id="report_explanation",
        title="检查报告解释",
        triggers=(
            "报告", "影像", "B超", "彩超", "CT", "核磁", "MRI", "X光", "心电",
            "结节", "囊肿", "息肉", "阴影", "钙化",
        ),
        guidance=(
            "解释检查报告时：\n"
            "1) 先说明检查类型、部位与结论性描述，再解释关键术语。\n"
            "2) 对「结节/囊肿/息肉/钙化」等描述，说明常见分级或随访惯例，"
            "并指出报告是否已给出分级（如 BI-RADS、TI-RADS、LI-RADS）。\n"
            "3) 明确区分「需要立即处理」「需要定期复查」「通常无需处理」。\n"
            "4) 指出报告解读的局限：影像描述依赖操作者与设备，最终判断需结合临床。\n"
            "5) 若报告缺页或关键结论缺失，直接说明信息不完整。"
        ),
    ),
]

SKILLS = {s.id: s for s in _SKILLS}


def list_skills() -> List[ClinicalSkill]:
    return list(_SKILLS)


def get_skill(skill_id: str) -> Optional[ClinicalSkill]:
    return SKILLS.get(skill_id)


def select_skills(text: str, limit: int = 2) -> List[ClinicalSkill]:
    """按问题文本选择相关 skill（关键词命中计数，确定性、可测试）。

    这里刻意不用模型做选择：技能选择是**规则可判**的，成本低且可解释；
    模型只在拿到技能后做语义判断。
    """
    if not text:
        return []
    scored: List[tuple[int, ClinicalSkill]] = []
    for skill in _SKILLS:
        hits = sum(1 for kw in skill.triggers if kw in text)
        if hits:
            scored.append((hits, skill))
    scored.sort(key=lambda x: (-x[0], x[1].id))
    return [s for _hits, s in scored[:limit]]


def render_skills_block(skills: List[ClinicalSkill]) -> str:
    """把选中技能渲染成提示词片段（注入 system 消息）。"""
    if not skills:
        return ""
    parts = ["## 临床思维框架（内部指引，不要原样复述给用户）"]
    for s in skills:
        parts.append(f"### {s.title}\n{s.guidance}")
    return "\n\n".join(parts)
