#!/usr/bin/env python
"""红旗规则初版导入（ARCHITECTURE §7 A8；PRD F-11/Q-5 草案，医学顾问评审后改本文件重跑）。

用法：cd server && python ../scripts/seed_redflag_rules.py
幂等：按 code upsert；每次更新 version+1（触发热更新）。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

from sqlalchemy import select  # noqa: E402

from app.common.ids import new_id  # noqa: E402
from app.db.models import RedFlagRule  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

# (code, name, category, keywords[], keyword_logic, context_logic, severity, message)
RULES = [
    (
        "RF_CHEST_PAIN_RADIATING", "胸痛伴放射痛", "成人急症",
        ["胸痛", "心口疼", "胸口压榨", "胸口像压了石头"],
        "any",
        {"all": [{"kw": "胸痛"},
                 {"any": [{"kw": "放射"}, {"kw": "左臂"}, {"kw": "后背"},
                          {"kw": "下颌"}, {"kw": "肩膀"}]}]},
        "high",
        "出现胸痛并向左臂/后背等放射，可能提示急性心肌梗死等危重情况，请立即拨打120或就近急诊就医。",
    ),
    (
        "RF_STROKE_SIGNS", "突发偏瘫/口角歪斜/言语不清", "成人急症",
        ["嘴歪", "口角歪斜", "偏瘫", "一侧肢体无力", "半边不听使唤",
         "言语不清", "说话不清", "口齿不清", "看东西重影"],
        "any",
        None,
        "high",
        "突然出现口角歪斜、一侧肢体无力或言语不清，可能为急性脑卒中，请立即拨打120就医（越快越好）。",
    ),
    (
        "RF_CHILD_FEBRILE_SEIZURE", "儿童高热惊厥", "儿童急症",
        ["抽搐", "惊厥", "眼睛上翻", "四肢抖动"],
        "any",
        {"all": [{"any": [{"kw": "孩子"}, {"kw": "宝宝"}, {"kw": "小儿"},
                          {"kw": "儿童"}, {"kw": "儿子"}, {"kw": "女儿"}]},
                 {"any": [{"kw": "发热"}, {"kw": "发烧"}, {"kw": "高热"}, {"kw": "39"}, {"kw": "40"}]}]},
        "high",
        "儿童发热伴抽搐/惊厥可能为高热惊厥或更严重问题，请立即就医急诊处理。",
    ),
    (
        "RF_SUDDEN_SEVERE_HEADACHE", "突发剧烈头痛", "成人急症",
        ["剧烈头痛", "爆炸样头痛", "一生中最厉害的头痛", "霹雳样头痛", "头痛欲裂"],
        "any",
        None,
        "high",
        "突发剧烈头痛可能提示脑出血等急症，请立即就医。",
    ),
    (
        "RF_SEVERE_DYSPNEA", "严重呼吸困难", "成人急症",
        ["喘不上气", "呼吸困难", "窒息感", "严重气短", "憋气憋得厉害"],
        "any",
        None,
        "high",
        "出现明显呼吸困难可能为急性心肺疾病，请立即就医或拨打120。",
    ),
    (
        "RF_SYNCOPE", "晕厥/意识丧失", "成人急症",
        ["晕厥", "昏厥", "晕倒", "失去意识", "不省人事"],
        "any",
        None,
        "high",
        "发生晕厥或意识丧失需要尽快就医查明原因，期间请勿独自行动、避免驾驶。",
    ),
    (
        "RF_ACUTE_ABDOMEN", "剧烈腹痛", "成人急症",
        ["剧烈腹痛", "肚子剧痛", "刀割样痛", "腹痛难忍"],
        "any",
        None,
        "high",
        "剧烈腹痛可能提示急腹症（如消化道穿孔、急性胰腺炎），请立即就医。",
    ),
    (
        "RF_PERSISTENT_VOMITING", "持续呕吐/无法进食", "成人急症",
        ["一直吐", "持续呕吐", "喷射性呕吐", "喝水都吐"],
        "any",
        None,
        "medium",
        "持续呕吐可能导致脱水；若伴随剧烈头痛或腹痛请尽快就医。",
    ),
    (
        "RF_HIGH_FEVER_PERSISTENT", "持续高热不退", "通用急症",
        ["高烧不退", "持续高热", "烧了三天", "39度以上"],
        "any",
        None,
        "medium",
        "持续高热建议尽快就医明确原因；如为儿童、老人或精神状态变差，请立即就医。",
    ),
    (
        "RF_BP_EXTREME", "血压危象", "成人急症",
        ["血压高", "血压飙升", "高血压"],
        "any",
        {"all": [{"any": [{"kw": "血压"}, {"kw": "高压"}, {"kw": "低压"}]},
                 {"any": [{"obs": {"code": "BP_SYS", "gte": 180}},
                          {"obs": {"code": "BP_DIA", "gte": 110}}]}]},
        "high",
        "血压达到危象水平（≥180/110mmHg）并伴不适时，请立即就医；若同时有头痛、胸痛、视物模糊请拨打120。",
    ),
    (
        "RF_MENTAL_HEALTH_CRISIS", "心理危机信号", "通用急症",
        ["不想活", "自杀", "轻生", "结束生命", "活着没意思"],
        "any",
        None,
        "high",
        "您/家人表达了危险的心理信号，请立即寻求帮助：可拨打24小时心理援助热线（如12356）或前往精神心理科就诊，确保有人陪伴。",
    ),
]


def upsert(db) -> None:
    created = updated = 0
    for code, name, category, keywords, logic, ctx, severity, message in RULES:
        row = db.scalar(select(RedFlagRule).where(RedFlagRule.code == code))
        if row is None:
            db.add(
                RedFlagRule(
                    id=new_id(), code=code, name=name, category=category,
                    keywords=keywords, keyword_logic=logic, context_logic=ctx,
                    severity=severity, message=message, action="refer_now",
                    enabled=True, version=1,
                )
            )
            created += 1
        else:
            changed = (
                row.keywords != keywords
                or row.context_logic != ctx
                or row.severity != severity
                or row.message != message
                or row.name != name
            )
            row.name = name
            row.category = category
            row.keywords = keywords
            row.keyword_logic = logic
            row.context_logic = ctx
            row.severity = severity
            row.message = message
            row.enabled = True
            if changed:
                row.version += 1  # AC-11.3 热更新版本推进
                updated += 1
    db.commit()
    print(f"seed_redflag_rules done: total={len(RULES)} created={created} updated={updated}")


def main() -> None:
    db = SessionLocal()
    try:
        upsert(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
