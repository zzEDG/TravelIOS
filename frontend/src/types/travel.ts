export type Skill = 'CITYWALK' | 'ATTRACTION' | 'FOOD'
export interface Location { latitude: number; longitude: number; coordinate_system: 'GCJ-02' | 'WGS84' }
export interface Meta { degraded_services: string[]; route_confidence: number | null }
export interface TripNarration {
  narrator: 'CityWalkNarratorAgent' | 'AttractionNarratorAgent' | 'FoodNarratorAgent'
  evaluation: string; changes: string[]; based_on_version: number | null
  stay_recommendations?: { poi_id: string; suggestion: string }[]
  user_request: string | null; source: 'LLM' | 'RULES'
}
export interface TravelPlan {
  trip_id: string; state_version: number; routed_skill: Skill
  title: string; summary: string; origin: Location
  places: { step_order: number; stay_minutes: number | null; distance_from_origin_meters?: number | null; why_selected: string; poi: {
    id: string; name: string; category: string; address: string; location: Location
    source_url: string | null; retrieved_at: string | null
  } }[]
  routes: { from_poi_id: string | null; to_poi_id: string; distance_meters: number | null; duration_minutes: number | null; polyline: string | null }[]
  estimated_minutes: number | null; total_walking_meters: number | null
  duration_semantics?: 'TOTAL_ESTIMATE' | 'WALKING_ONLY'; warning_codes?: string[]
  weather: { source: 'AMAP' | 'QWEATHER'; source_url?: string | null; temperature_max_c?: number | null; temperature_min_c?: number | null; precipitation_probability_day?: number | null; precipitation_probability_night?: number | null; forecast_date: string; day_condition: string; night_condition: string; day_temperature_c: number | null; night_temperature_c: number | null } | null
  cover: { url: string; alt: string; photographer: string; photographer_url: string; unsplash_url: string } | null
  notes: string[]; route_semantics?: 'ALTERNATIVES'
  narration?: TripNarration | null
}
export interface Clarification { routed_skill: 'CLARIFY'; question: string; trip_id: string | null; state_version: number | null }
export interface Envelope<T> { code: number; message: string; data: T; meta: Meta }
export interface TripState {
  trip_id: string; state_version: number; current_skill: Skill; city: string; theme: string; current_location: Location; origin_name?: string
  available_hours: number; target_stops: number; max_walking_meters: number | null; avoid_commercial: boolean
  travel_date: string | null; additional_requirements: string; interests: string[]; dislikes: string[]
  visit_order: string[]; end_with: string | null
  radius_meters: number; consider_weather: boolean; meal_type: string; cuisine_preference: string[]
  food_avoid: string[]; max_distance_meters: number; food_limit: number
}
export interface Snapshot { state: TripState; latest_plan: TravelPlan; latest_version: number }
export interface HistoryItem {
  trip_id: string; state_version: number; latest_version: number; skill: Skill
  city: string; title: string; summary: string; origin_name: string; place_count: number
  created_at: string; updated_at: string; expired: boolean
}
export interface HistoryPage { items: HistoryItem[]; total: number; page: number; page_size: number }
export const skillNames: Record<Skill, string> = { CITYWALK: '城市漫步', ATTRACTION: '景点游览', FOOD: '餐厅推荐' }
export interface LocationMatch {
  id: string; name: string; address: string; category: string; location: Location; source_url: string | null
}
