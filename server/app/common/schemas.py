"""Pydantic 基类：字段 camelCase（§8.3 alias_generator）。"""

from pydantic import BaseModel, ConfigDict


def to_camel(name: str) -> str:
    """snake_case → camelCase（如 subject_id → subjectId）。"""
    parts = name.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


class CamelModel(BaseModel):
    """API 出入参统一基类：camelCase 字段名，兼容 snake_case 输入。"""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    def to_api(self) -> dict:
        """序列化为 API 响应形态（by_alias）。"""
        return self.model_dump(by_alias=True, exclude_none=False)
