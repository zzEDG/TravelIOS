# TravelOS 前端接入说明

## 页面与请求流程

Vue 3 + TypeScript + Ant Design Vue，保留原有绿色卡片布局，无新增依赖。

- 每个子系统的“我的规划历史”按能力筛选，可搜索城市/标题/起点、分页和刷新。点击“查看”打开指定版本；“载入修改”使用 /?trip=...&version=... 回填该快照的偏好和起点。
- 结果页“历次修改”显示已保存版本；保存修改时提交当前最新 state_version 与所选 base_version，新结果生成新版本，旧快照保留。可以基于旧版本重新规划，冲突返回409并提示重新载入。
- 首页 /：城市漫步、景点游览、餐厅推荐三个表单，分别请求 POST /api/v1/citywalk/plan、/attraction/plan、/food/search。第四个Tab“对话规划”请求 /api/v1/travel。
- 起点输入城市与地名/地址，例如人民广场地铁站。点击“搜索起点”，通过 /api/v1/locations/search 返回候选，核对名称/地址后选择；程序自动取得GCJ-02坐标。没有默认坐标，也不会自动获取用户位置。修改城市或位置文本会清除旧选择。日期可选。起点名称随行程保存和回填，旧数据沿用已保存坐标。
- 结果页 /trips/:id：用 GET /api/v1/trips/{id} 读取后端SQLite中的结果；刷新页面也能恢复。展示地点、路段、来源链接、估计停留、可用天气与缺失提示。
- Food展示独立备选，每家从起点出发，不显示累计时长或串联总距离；CityWalk/Attraction显示游览顺序及可用总距离。
- “载入这个版本修改”回到 /?trip=...&version=...，读取指定状态并回填表单。同一能力提交trip_id/state_version/base_version，成功后递增版本；切换表单能力明确新建行程，想沿用原trip_id则使用对话切换。
- 结果页对话使用当前trip_id/state_version，不成功就保留原结果。CLARIFY展示问题，下一轮携带之前需求和澄清问题；成功后刷新保存的状态。
- localStorage只记最后一个trip_id，计划与偏好由后端保存。浏览器禁用本地存储仍可用URL恢复。过期或无效ID显示后端错误和返回首页入口。
- 参数错误、网络超时、版本冲突显示在页面。冲突时保留输入，刷新后再提交；没有自动重试付费规划请求。等待期间禁止重复提交。
- 地点分布图使用 /api/v1/maps/static（旧/api/walk/map仍兼容），起点加最多5个推荐地点，共6点；图片失败时显示替代提示。不是可交互的路线导航图。
- /result旧页面及旧Walk类型保留兼容既有sessionStorage结果，新流程不用sessionStorage缓存计划。

## 改动文件

frontend/src/views/Home.vue：三个表单、统一对话、回填与续接。
frontend/src/views/TravelResult.vue：新的共用结果页、路线语义、对话续接、恢复。
frontend/src/api/travel.ts：V1请求、统一错误转换、最后行程ID。
frontend/src/types/travel.ts：三能力结果、状态与澄清类型。
frontend/src/main.ts：/trips/:id路由。
frontend/src/App.vue、frontend/index.html：品牌与共用样式。
backend/app/controllers/maps.py：地图响应；services/map_service.py：地图点数支持2..6。

## 启动与调试

先启动后端，再在WSL交互式终端启动前端：

    cd /Projects/fullstack-learning-agent/frontend
    npm run dev -- --host 127.0.0.1

打开 http://localhost:5173。需要其他后端地址时，通过VITE_API_BASE_URL指定；里面只能放地址，不要放密钥。

本机非交互式WSL可能错误调用Windows的npm，此时先加载nvm：

    source ~/.nvm/nvm.sh
    nvm use 22
    npm run dev -- --host 127.0.0.1

调试链路：Home.submit / TravelResult.send → api/travel.ts → FastAPI对应端点 → MainAgent → 子 Agent → TripService → SQLite → GET恢复 → Vue响应式渲染。浏览器Network面板观察请求字段、trip_id、state_version和错误码。

## 规划历史验证（2026-10-12）

- 后端46项测试通过；新增历史测试覆盖三个子系统筛选、检索、分页、历史快照、旧版本编辑、跨能力历史、过期后显式重新规划、统一对话历史基础、版本冲突、旧数据库迁移和历史写入失败的事务回滚。
- 前端类型检查和生产构建通过；保留原有依赖体积提示。
- 浏览器通过临时SQLite与规划测试替身验证三个子系统的历史入口、参数回填和修改保存，城市漫步从历史版本1生成版本3后，版本1和2仍可查看。测试未向正式行程数据库写入模拟规划。
- 当前 backend/run.py 的调试启动采用 reload=False；修改后需要重新启动后端调试会话，再刷新 http://localhost:5173。首次读取会自动迁移正式数据库中仍然保留的快照。

## 本轮验证（2026-10-11）

- 后端29项自动化测试通过，包括地图6点支持及超限拒绝。
- TypeScript vue-tsc类型检查通过，Vite生产构建通过。依赖原有Ant Design Vue使打包存在体积提示，不影响构建。
- 浏览器连接真实FastAPI路由与临时SQLite，外部地图/翻译/Jev使用测试替身；没有写入正式历史，没有用这些模拟地点声称真实联调成功。
- 首页三个结构化表单均能提交并跳转相应结果。
- Food页面显示独立备选，不误加总路线。
- “修改当前行程”回填并续接，版本1→2；结果页对话切到Attraction，版本2→3。
- 刷新结果页仍恢复版本3；景点连续路段、停留估计和天气缺失提示显示正常。
- 页面提交测试发现Ant Design Vue表单需要model才能触发finish，已补齐。
- 上一阶段的真实高德/DeepSeek/Jev接口结果见README_V2_STAGE3.md；本轮页面联调以模拟外部服务为主。


## 位置输入补充验证

页面使用测试替身验证位置候选、选中显示和修改输入清除旧选择；未选择起点不能提交。真实高德服务搜索“上海 / 人民广场”返回20项，包括上海人民广场、人民广场和人民广场地铁站。地址回退、多个候选、起点名称保存及空输入通过自动化测试。

后端路由已按三能力加统一入口拆分，见 [路由结构](ROUTES.md)。


## 目录整理补充

前端页面路由现在统一在router/index.ts，路由名称/路径在config/routes.ts；API地址在config/endpoints.ts，HTTP客户端在api/http.ts。原services目录已拆到api目录。后端入口改为app.main:app，完整路径对照见 [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)。本轮36项后端测试、前端类型检查与构建通过，页面加载采用路由懒加载。


## 后端组织更新（2026-10-14）

仅后端调整为 Controller–Service–Model 分层，前端源码、页面路由和调用的 HTTP 接口均未调整。原 `api/routes` 改为 `controllers`，业务与存储路径参见 [当前目录说明](PROJECT_STRUCTURE.md)；上面的日期记录保留当时的验证背景。
