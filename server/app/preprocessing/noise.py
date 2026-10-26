"""报告页眉/页脚噪声判定（指标名清洗）。

真实体检报告 PDF 的页眉/页脚常含联系方式与机构信息，例如：

    全国统一报告解读专线:400 081 8899
    网址 www.ikang.com
    爱康国宾健康体检管理集团有限公司 版权所有

整行正则解析与 VLM 都可能把它们当成"指标"（raw_name=全国统一报告解读专线,
value=400），在确认卡片里表现为一堆无意义条目（2026-10-21 实测缺陷）。
本模块提供统一的**指标名**噪声判定，供规则解析与 schema 归一化共用。
"""

import re

# 联系方式 / 机构信息关键词：出现在指标名中即判定为非医学指标
_CONTACT = re.compile(
    r"专线|热线|电话|手机|客服|传真|邮箱|邮件|网址|网站|域名|邮编|地址|版权|"
    r"公众号|微信|二维码|扫码|下载|APP|体检号|卡\s*号|项目号|会员号|诊疗卡|发票|"
    r"版权所有|健康管理(集团|中心)?$",
    re.I,
)

# 纯机构/科室描述（无医学指标）
_ORG_ONLY = re.compile(
    r"^[一-龥A-Za-z0-9（）()·\s]*"
    r"(医院|门诊部|体检中心|健康管理中心|医疗中心|保健所|诊所)"
    r"[一-龥A-Za-z0-9（）()·\s]*$"
)

# 纯数字/符号名（如 "400"、"--"）
_NOT_MEASUREMENT = re.compile(r"^[\d\s\-—–_./:：,，、()（）%]+$")

# 明显的页眉标签（文案而非测量项）
_LABEL_ONLY = re.compile(
    r"^(报告|报告单|检验报告|检查报告|影像报告|体检报告|健康体检报告|"
    r"姓名|性别|年龄|科室|床号|科别|样本|标本|条码|编号|备注|"
    r"送检|检验者|审核者|打印|检查日期|报告日期|采样日期)$"
)

_MAX_NAME_LEN = 32


def is_noise_measurement(raw_name: str) -> bool:
    """指标名是否为页眉/页脚/联系方式噪声（应为 False 才保留）。"""
    name = (raw_name or "").strip()
    if not name:
        return True
    if len(name) > _MAX_NAME_LEN:
        return True
    if _NOT_MEASUREMENT.match(name):
        return True
    if _LABEL_ONLY.match(name):
        return True
    if _ORG_ONLY.match(name):
        return True
    if _CONTACT.search(name):
        return True
    return False
