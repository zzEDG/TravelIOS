"""识别明确的地点数量指令；时间、距离和含糊表达仍交给需求模型处理。"""
import re
from dataclasses import dataclass

_NUMBER = r"(?:[0-9]+|[零一二两三四五六七八九十]+)"
_NOUN = r"(?:地点|景点|站点|目的地|餐厅|饭店|餐馆|点)"
_ACTION = r"(?:增加到|加到|减少到|调整为|调整到|改成|改为|换成|变成|设为|只要|只去|安排|规划|推荐|新增|再增加|再加上|再加|多加|加上|增加|添加|加|减少|减掉|少去|多去)"
_SPEC = rf"(?P<action>{_ACTION})?\s*(?P<number>{_NUMBER})\s*(?P<unit>个|家|处|站)\s*(?P<noun>{_NOUN})?"
_OPTIONAL_UNIT = _SPEC.replace("(?P<unit>个|家|处|站)", "(?P<unit>个|家|处|站)?")
_COUNT_ONLY = re.compile(
    rf"^(?:请|麻烦|帮我|请帮我|我想|我要|我想要)?\s*(?:把)?"
    rf"(?P<label>(?:地点|景点|站点|餐厅|饭店)(?:数目|数量|数))?\s*"
    rf"{_OPTIONAL_UNIT}\s*(?:就好|就行|即可|吧)?[。！!，,]?$"
)
_EXPLICIT = re.compile(_SPEC)


def _number(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = dict(zip("零一二两三四五六七八九", (0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
    if "十" in value:
        left, _, right = value.partition("十")
        return (digits.get(left, 1) * 10) + digits.get(right, 0)
    if value not in digits:
        raise ValueError("请用一个明确的数字填写地点数量")
    return digits[value]


@dataclass(frozen=True)
class CountEdit:
    value: int
    delta: int
    only_count: bool
    restaurant: bool
    sightseeing: bool

    def resolve(self, previous, destination: str) -> int:
        if not self.delta:
            return self.value
        if previous is None or previous.state.current_skill != destination:
            raise ValueError("增加或减少地点时，请先载入行程，或直接说明目标总数")
        return len(previous.latest_plan.places) + self.delta * self.value

    def can_keep_agent(self, previous) -> bool:
        return bool(
            previous and self.only_count
            and self.applies_to(previous.state.current_skill)
        )

    def applies_to(self, destination: str) -> bool:
        if self.restaurant:
            return destination == "FOOD"
        if self.sightseeing:
            return destination != "FOOD"
        return True


def count_edit(message: str) -> CountEdit | None:
    """仅确定表达清楚的数量，不猜测混合目标、否定、范围或对比句。"""
    text = message.strip()
    exact = _COUNT_ONLY.fullmatch(text)
    if exact and not (exact.group("unit") or exact.group("label")):
        exact = None
    if exact:
        match = exact
    else:
        # 复杂句仍由模型理解；只覆盖单个有地点名词的明确数量。
        if re.search(r"不要|不是|不想|不(?:再)?(?:新增|增加|添加|加上|加|多加)|无需(?:新增|增加|添加|加)|别|至少|最多|不超过|以内|或者|还是|或|上次|以前|之前|昨天|[到至~-]\s*" + _NUMBER, text):
            return None
        matches = [m for m in _EXPLICIT.finditer(text) if m.group("noun")]
        if len(matches) != 1:
            return None
        match = matches[0]
    action = match.group("action") or ""
    label = match.groupdict().get("label") or ""
    delta = 1 if action in ("再加", "再加上", "再增加", "新增", "多加", "加上", "加", "增加", "添加", "多去") else (
        -1 if action in ("减少", "减掉", "少去") else 0
    )
    return CountEdit(
        value=_number(match.group("number")), delta=delta, only_count=exact is not None,
        restaurant=match.group("unit") == "家" or match.group("noun") in ("餐厅", "饭店", "餐馆") or label.startswith(("餐厅", "饭店")),
        sightseeing=match.group("noun") in ("景点", "站点", "目的地") or label.startswith(("景点", "站点")),
    )
