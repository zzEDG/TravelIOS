"""城市漫步偏好解析的数据契约。"""

from pydantic import BaseModel, Field, field_validator, model_validator
from app.models.travel import PreferenceList


class SearchPreference(BaseModel):
    query: str = Field(min_length=1, max_length=40)
    english: str = Field(min_length=1, max_length=160)

    @field_validator('query')
    @classmethod
    def nonempty_query(cls, value):
        if not value.strip():
            raise ValueError('检索词不能为空')
        return value.strip()

class WalkSearchDraft(BaseModel):
    """模型输出草稿；先应用用户明确字段，再检查字段间的引用关系。"""

    searches: list[SearchPreference] = Field(min_length=1, max_length=10)
    request_english: str = Field(min_length=1, max_length=2000)
    visit_order: PreferenceList = Field(default_factory=list)
    end_with: str | None = Field(default=None, min_length=1, max_length=40)

class WalkSearchProfile(WalkSearchDraft):
    @model_validator(mode='after')
    def valid_references(self):
        keys = [item.query for item in self.searches]
        if len(set(keys)) != len(keys):
            raise ValueError('检索偏好不能重复')
        if any(value not in keys for value in self.visit_order):
            raise ValueError('顺序中的偏好必须有对应检索词')
        if self.end_with is not None and self.end_with not in keys:
            raise ValueError('最后一站偏好必须有对应检索词')
        return self

    @property
    def keywords(self):
        return {item.query: item.english for item in self.searches}
