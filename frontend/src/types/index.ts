export interface WalkRequest {
  city: string
  theme: string
  duration_hours: number
  start_place: string
  visit_date: string | null
  notes: string
}
export interface Stop {
  poi_id: string
  name: string
  address: string
  category: string
  longitude: number
  latitude: number
  stay_minutes: number
  reason: string
  amap_url: string
}
export interface WalkLeg {
  from_name: string
  to_name: string
  distance_m: number | null
  duration_min: number | null
}
export interface Weather {
  source?: 'AMAP' | 'QWEATHER'
  temperature_max_c?: number | null
  temperature_min_c?: number | null
  precipitation_probability_day?: number | null
  date: string
  day_weather: string
  night_weather: string
  day_temp: string
  night_temp: string
}
export interface CoverPhoto {
  url: string
  photographer: string
  photographer_url: string
  unsplash_url: string
  alt: string
}
export interface WalkPlan {
  city: string
  theme: string
  title: string
  introduction: string
  duration_hours: number
  stops: Stop[]
  legs: WalkLeg[]
  estimated_minutes: number
  weather: Weather | null
  rain_tip: string
  cover: CoverPhoto | null
  map_points: string
  notes: string[]
}
export interface WalkResponse {
  success: boolean
  data: WalkPlan
}
