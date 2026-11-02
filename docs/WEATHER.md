# 当前天气服务：和风

城市漫步（含旧接口）和景点游览默认使用 QWeatherService；餐厅不查询天气。景点关闭 consider_weather 时跳过天气查询。

## 配置与调用

读取 QWEATHER_API_HOST 和 QWEATHER_API_KEY，或者 QWEATHER_JWT（优先使用JWT）。当前用户已有配置，无需重新填写。
详见 [QWEATHER.md](QWEATHER.md)。

agents/citywalk_agent.py 和 skills/attraction.py 初始化默认服务，调用 integrations/qweather_service.py 的 lookup，使用起点坐标及出行日期获取每日预报。返回 source=QWEATHER，页面显示和风、昼夜天气、温度和可用的降水概率。

网络、权限或日期错误时显示具体提示并保留路线；不自动切换到高德。高德天气适配器保留供显式注入使用，历史记录的来源保持原值。

重启后端并重新生成行程生效。读取已有行程不会重新查询天气。

## 验证记录

2026-10-11：使用现有配置真实请求上海坐标121.47,31.23成功，source=QWEATHER；此前的 Security Restriction 已不再出现。未调用模型重新生成完整行程。

切回后40项后端测试通过，并通过景点子系统默认天气服务再次真实查询成功。前端已有按source显示来源的逻辑，本次无需修改页面。
