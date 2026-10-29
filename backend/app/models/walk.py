"""城市主题漫游的数据契约。"""

from datetime import date
from typing import List, Optional, Literal

from pydantic import BaseModel, Field


class WalkRequest(BaseModel):
    city: str = Field(..., min_length=2, max_length=30)
    theme: str = Field(default="自由漫步", min_length=2, max_length=40)
    duration_hours: int = Field(default=3, ge=2, le=8)
    start_place: str = Field(default="", max_length=100)
    visit_date: Optional[date] = None
    notes: str = Field(default="", max_length=500)
    interests: list[str] = Field(default_factory=list, max_length=10)
    dislikes: list[str] = Field(default_factory=list, max_length=10)
    visit_order: list[str] = Field(default_factory=list, max_length=10)
    end_with: str | None = Field(default=None, min_length=1, max_length=40)
    avoid_commercial: bool = False


class Stop(BaseModel):
    poi_id: str
    name: str
    address: str
    category: str
    longitude: float
    latitude: float
    stay_minutes: int
    reason: str
    amap_url: str


class WalkLeg(BaseModel):
    from_name: str
    to_name: str
    distance_m: Optional[int] = None
    duration_min: Optional[int] = None
    from_poi_id: Optional[str] = None
    to_poi_id: Optional[str] = None
    polyline: Optional[str] = None


class Weather(BaseModel):
    source: Literal["AMAP", "QWEATHER"] = "AMAP"
    source_url: str | None = None
    temperature_max_c: float | None = None
    temperature_min_c: float | None = None
    precipitation_probability_day: float | None = None
    precipitation_probability_night: float | None = None
    date: str
    day_weather: str
    night_weather: str
    day_temp: str
    night_temp: str


class CoverPhoto(BaseModel):
    url: str
    photographer: str
    photographer_url: str
    unsplash_url: str
    alt: str


class WalkPlan(BaseModel):
    city: str
    theme: str
    title: str
    introduction: str
    duration_hours: int
    stops: List[Stop]
    legs: List[WalkLeg]
    estimated_minutes: int
    weather: Optional[Weather] = None
    rain_tip: str
    cover: Optional[CoverPhoto] = None
    map_points: str
    notes: List[str] = Field(default_factory=list)


class WalkResponse(BaseModel):
    success: bool = True
    data: WalkPlan
