# 和风天气接入

当前默认天气服务已恢复为和风。2026-10-11 使用现有 Host/API Key 复测成功，之前的 403 已解除；下方“未配置”和“403”均为历史排查记录。当前概览见 [WEATHER.md](WEATHER.md)。

城市漫步（包括旧接口）与景点游览改用和风每日预报；餐厅备选不请求天气。复用起点坐标，按指定出行日期取预报；未配置、权限不足、网络异常、日期超出预报范围时保留地点与路线并明确提示。景点关闭consider_weather时不请求。

使用当前官方WebAPI v1：GET https://专属Host/weather/v1/daily/{latitude}/{longitude}，days=10、localTime=true、lang=zh。纬度在前，经度在后，精度2位。认证优先QWEATHER_JWT Bearer，未设置JWT时使用QWEATHER_API_KEY的X-QW-Api-Key头。不将密钥放URL或传给浏览器。

模块：integrations/qweather_service.py负责请求/解析；CityWalkAgent和AttractionAgent负责消费；WeatherState保留旧字段并增加最高最低温度、昼夜降水概率与来源。页面根据来源显示和风或旧高德记录，温度与概率取真实数值。不会把降水量当概率，也不会根据天气猜地点是否室内。

官方文档：
- https://dev.qweather.com/docs/api/weather/weather-daily-forecast/
- https://dev.qweather.com/docs/configuration/authentication/
- https://dev.qweather.com/docs/configuration/api-host/


## 配置与启动

在 backend/.env 或启动进程可见的系统环境变量中填写：

    QWEATHER_API_HOST=你的专属域名.qweatherapi.com
    QWEATHER_API_KEY=你的和风API_KEY

Host从和风控制台“设置”读取；不能使用旧公共devapi.qweather.com地址。也可用QWEATHER_JWT替代API_KEY，两者均配置时仅发送JWT。JWT为已生成的有效Token，本轮不在程序中自动签发或续期；过期后更换并重启。API_KEY的认证限制会变化，长期运行推荐使用JWT，具体以官方文档为准。

实际文件示例只留空位，没有写入真实凭据。不需要修改前端，不放到VITE_变量。填完重启后端，重新生成或更新行程；旧SQLite结果不会自动改变天气来源。

和风未配置不会阻止后端启动，控制台显示“未配置（天气会降级）”；行程notes说明具体原因，meta.degraded_services包含WEATHER。没有静默回退到高德，也不会拿今天的天气代替所选日期。景点关闭天气开关则不查询，也不标记缺失。

## 字段及边界

WeatherState.source支持AMAP与QWEATHER，默认AMAP保证旧记录兼容。新查询来源为QWEATHER，source_url指向和风。
- temperature_max_c / temperature_min_c：全天最高/最低温度，摄氏度。
- day_temperature_c / night_temperature_c：兼容字段，本轮和风映射白天最高及夜间最低温度。
- precipitation_probability_day / precipitation_probability_night：0..1，前端乘100显示百分比，未知为null。
- rain_probability保留旧字段，不将所有降水概率当成下雨概率；is_outdoor_favorable保持null，不擅自做安全判断。
- 出行日期采用Asia/Shanghai，空日期默认中国当前日；10天范围是今天至第9天。以daytime.forecastStartTime的当地日期匹配，避免预报开始在前一天晚间的跨日错误。
- 天气使用起点附近两位小数的坐标网格，不是每个景点的精确微气候。天气作为出行信息与CityWalk文案输入；本轮不会强制替换成“室内景点”，因为POI没有经过核实的室内属性。

## 实现与验证（2026-10-11）

integrations/qweather_service.py：12秒超时、专属Host校验、单一头部认证、v1每日预报解析、错误信息脱敏。
agents/citywalk_agent.py：旧接口及新CityWalk共用和风数据，并保留原天气返回格式。
skills/citywalk.py：转成扩展WeatherState。
skills/attraction.py：复用起点/日期，遵守consider_weather。
frontend/src/views/TravelResult.vue和Result.vue：和风标注、温度范围、降水概率；来源标注与链接。

34项后端测试通过，前端类型检查及生产构建通过。新增测试验证v1路径和纬经顺序、API_KEY头不放URL、JWT优先、日期过滤、HTTP401/未配置/范围错误、缺失概率不变成0、景点开关及数据消费。原CityWalk离线测试改为显式注入天气替身。

当前进程未发现和风专属Host和认证凭据，因此只完成模拟协议与集成验证；尚未声称真实和风调用成功。配置后可在qweather_service.forecast设置断点，再从页面生成城市漫步或景点行程查看weather.source是否QWEATHER。

## 配置后仍不可用：2026-10-11 实测

本次通过 agents-learn 环境加载项目配置，确认 QWEATHER_API_HOST 和 QWEATHER_API_KEY 均已读取，JWT 未配置。实际调用每日天气预报返回 HTTP 403，错误类型为 Security Restriction。此前“未发现凭据”的记录仅代表当时状态。

此错误表示命中了和风控制台设置的请求限制，不代表环境变量未加载；和风不会指出具体命中的规则。进入控制台“项目管理”，选择当前 Key 对应的凭据，检查“应用限制”和“API限制”，确保允许当前后端请求每日天气预报。按实际规则修正，不要盲目更换密钥或充值。

代码已将这种错误单独映射为明确中文提示，不透传响应正文或凭据。修正控制台限制后重新生成行程；只刷新历史记录不会重新获取天气。本次尚未获取成功的真实天气数据。

参考：[官方错误码](https://dev.qweather.com/docs/resource/error-code/#security-restriction)、[凭据安全限制](https://dev.qweather.com/docs/configuration/project-and-key/#安全限制)。
