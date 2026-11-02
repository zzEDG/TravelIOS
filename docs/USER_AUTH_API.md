# TravelOS 用户登录与规划隔离接口规范

本文描述已实现的用户认证与行程隔离；规划字段仍以 [TravelOS 三子系统接口规范](TRAVELOS_API.md) 和实际 OpenAPI 为准。本文示例中的账号、用户 ID、行程 ID 和时间均为虚构数据。Base URL 为 `/api/v1`，JSON 接口使用 `Content-Type: application/json`。

目标：用户注册和登录后，只能访问自己的规划及历史版本；同一个账号可拥有多个独立行程。既有规划数据迁移由管理员确认，不能默认分配给第一个注册用户。

## 共同规则

1. Web 前端使用服务端会话 Cookie。前端不提交用户 ID；后端从已验证会话取得当前用户，查询及写入时必须校验归属。
2. 规划相关接口要求登录；本设计不提供匿名创建或匿名续接。原有规划请求字段、`trip_id` / `state_version` / `base_version` 的语义保持不变。
3. Cookie 名称 `travelos_session`，`HttpOnly`、`Path=/api/v1`、`SameSite=Lax`，生产环境通过 HTTPS 设置 `Secure`；开发环境允许本机 HTTP，不能沿用到生产环境。首次登录、重新登录都会签发新的随机会话令牌，不复用已有令牌。
4. 会话绝对有效期暂定 7 天，不随访问自动延长；退出后立即撤销当前会话。过期、撤销和用户禁用后不能继续使用。每次登录创建独立会话，允许多设备并存。
5. 浏览器请求使用 `credentials: "include"`；CORS 只允许配置的前端来源，启用凭据，不能使用 `Access-Control-Allow-Origin: *`。推荐同源部署或开发代理。前后端必须使用一致主机名，例如均用 `localhost`；`localhost` 与 `127.0.0.1` 不是同一主机。
6. 防 CSRF：所有浏览器状态变更请求，包括注册、登录、退出和规划 POST，后端校验 `Origin` 属于配置的前端来源；没有合法 `Origin` 的浏览器请求拒绝。已登录的状态变更请求额外要求 `X-CSRF-Token`，服务端验证其与当前会话绑定。受信任的非浏览器客户端如使用 Cookie，也需按同样规则发送允许的 `Origin` 和 CSRF 值。`SameSite=Lax` 不替代校验。
7. CSRF 值由服务端密钥和当前随机会话令牌计算，使用独立用途的 HMAC-SHA-256 及固定上下文；`GET /auth/csrf` 可重新取得。服务端通过常量时间比较验证，不在日志中记录 Cookie、CSRF 值、密码、请求体或完整查询字符串。生产环境必须配置独立强随机密钥，缺失时拒绝启动。
8. 同站点前端部署使用 `SameSite=Lax`。真正跨站部署需要重新评估 `SameSite=None; Secure`、第三方 Cookie 限制、CORS 和 CSRF；不视为本文默认已支持。

## 响应与错误格式

成功响应沿用 `code`、`message`、`data`、`meta` 外壳。认证接口不调用 Jev，**认证响应 meta 只包含 `schema_version`**，不要虚构 `decision_source=JEV`。既有规划响应格式不变。

```json
{
  "code": 0,
  "message": "success",
  "data": {},
  "meta": {"schema_version": "1.0"}
}
```

失败响应沿用现有错误外壳 `code`、`message`、`details`，不返回密码哈希或会话令牌；下表为认证错误码；行程错误码沿用现有定义。

| HTTP | code | message 示例 | 说明 |
|---|---:|---|---|
| 400 | 40010 | 当前请求来源不允许 | `Origin` 不合法或缺失 |
| 401 | 40101 | 请先登录或登录已过期 | 会话缺失、过期、撤销或用户不可用 |
| 401 | 40102 | 账号或密码错误 | 登录失败统一提示，不区分账号是否存在 |
| 403 | 40301 | 请求验证失败 | 已登录写请求的 CSRF 值缺失或不匹配 |
| 404 | 40004 | 行程不存在 | 无此行程或不属于当前用户，返回相同响应 |
| 409 | 40005 | 行程已被其他请求更新 | 沿用现有状态版本冲突语义 |
| 409 | 40901 | 用户名已被使用 | 用户名唯一约束冲突 |
| 422 | 40001 | 请求参数无效 | 参数校验失败；不回显密码 |
| 429 | 42901 | 请求过于频繁，请稍后重试 | 账号、来源等维度触发登录限流，可返回 `Retry-After` |

## 1. 用户注册：POST /auth/register

无需登录；校验 `Origin`。注册只创建账号，**不自动登录**。

请求：

```json
{"username": "traveler_01", "password": "Example-passphrase-2026"}
```

| 字段 | 约束 | 说明 |
|---|---|---|
| username | 3–32 字符，只允许 ASCII 字母、数字、下划线 | 去除首尾空格，统一转小写存储与查询；注册时由数据库唯一约束保证无竞态 |
| password | 12–128 个 Unicode 字符，UTF-8 不超过 512 字节 | 不静默截断、不去首尾空格；不要求特定字符组合，当前拒绝一小组内置常见密码；完整泄露密码库校验尚未接入。禁止记录明文 |

成功：HTTP 201。

```json
{
  "code": 0,
  "message": "success",
  "data": {"id": "c794f7fc-c316-478f-b409-d4f3c87d2025", "username": "traveler_01", "created_at": "2026-11-01T02:00:00Z"},
  "meta": {"schema_version": "1.0"}
}
```

此设计返回用户名重复信息，便于注册表单提示；这也会暴露用户名是否已注册，是否接受该权衡需在上线前确认。

## 2. 用户登录：POST /auth/login

无需登录；校验 `Origin`。请求字段与注册相同。服务端规范化用户名，验证密码和账号状态；不存在用户时也执行固定虚拟哈希校验，统一错误消息并限制登录尝试。参数超出合理上限时直接拒绝。

成功：HTTP 200，响应设置 `travelos_session` 的 `Set-Cookie`，`data` 不包含会话令牌。示例 cookie 值仅表示格式，并非可用凭据：

```http
Set-Cookie: travelos_session=<opaque-token>; Path=/api/v1; Max-Age=604800; HttpOnly; Secure; SameSite=Lax
```

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "user": {"id": "c794f7fc-c316-478f-b409-d4f3c87d2025", "username": "traveler_01", "created_at": "2026-11-01T02:00:00Z"},
    "expires_at": "2026-11-08T02:00:00Z",
    "csrf_token": "<csrf-token>"
  },
  "meta": {"schema_version": "1.0"}
}
```

密码错误或账号不可用返回 `401 / 40102`。登录失败不得产生有效会话。前端只在内存中保存 `csrf_token`；刷新后通过下一接口重新获取。

## 3. 获取 CSRF：GET /auth/csrf

要求有效会话；返回当前会话绑定的 CSRF 值。响应设置 `Cache-Control: no-store`，且禁止不受信任的跨域来源读取。

```json
{"code": 0, "message": "success", "data": {"csrf_token": "<csrf-token>"}, "meta": {"schema_version": "1.0"}}
```

会话过期或失效时返回 `401 / 40101`。

## 4. 当前用户：GET /auth/me

要求有效会话；响应设置 `Cache-Control: no-store`。返回当前账号信息，不返回密码哈希、会话令牌或数据库内部安全信息。

```json
{
  "code": 0,
  "message": "success",
  "data": {"id": "c794f7fc-c316-478f-b409-d4f3c87d2025", "username": "traveler_01", "created_at": "2026-11-01T02:00:00Z"},
  "meta": {"schema_version": "1.0"}
}
```

## 5. 退出登录：POST /auth/logout

有效会话必须携带允许的 `Origin` 和 `X-CSRF-Token`；撤销当前会话，并用相同 Cookie 名称和路径清除 Cookie。其他设备的会话保留。若会话已经不存在或过期，校验 `Origin` 后仍清除 Cookie 并返回成功；操作可重复。已登录但 CSRF 错误返回 403，不撤销会话。

```json
{"code": 0, "message": "success", "data": null, "meta": {"schema_version": "1.0"}}
```

## 6. 规划接口的登录与归属

| 接口 | 新增规则 |
|---|---|
| `POST /travel` | 必须登录并通过 CSRF；新建保存为当前用户的行程，续接或使用 `base_version` 时先验证归属 |
| `POST /citywalk/plan`、`POST /attraction/plan`、`POST /food/search` | 同上；保留既有请求字段和独立能力续接规则 |
| `GET /trips` | 必须登录；只列出当前用户的规划和历史，搜索、分页的总数也只统计当前用户 |
| `GET /trips/{trip_id}` | 必须登录；读取当前快照或 `version` 指定版本前校验归属 |
| `GET /trips/{trip_id}/history` | 必须登录；只读取当前用户对应行程的历史版本 |
| `POST /locations/search` | 本设计要求登录和 CSRF；它虽不保存行程，仍会调用外部服务 |
| `GET /maps/static` | 本设计要求登录；该接口根据提供的坐标生成图片，不作为行程归属判断依据 |

续接示例，Cookie 由浏览器自动发送：

```http
POST /api/v1/travel
Content-Type: application/json
Origin: https://travel.example.com
X-CSRF-Token: <csrf-token>
```

```json
{"trip_id": "trip_123456789abc", "state_version": 1, "message": "增加一家咖啡馆"}
```

后端依次验证会话、来源与 CSRF，再以当前用户限定行程查询；确认归属后才读取历史、锁定地点和执行外部规划。保存时在同一数据库事务中再次按用户、行程和预期版本校验并更新，追加历史；越权不得因竞争条件读写。其他用户的行程 ID 即使有效，也返回 `404 / 40004`。

现有 `/api/walk/*` 旧路由已停止注册并返回404，统一使用 `/api/v1` 接口，防止绕过用户隔离。面向公开访问的 API 文档和健康检查可单独配置公开范围。

## 7. 数据库设计（PostgreSQL）

现有 `trips` 保存最新快照，`trip_history` 保存历史版本；新增 `users`、`user_sessions`，并在 `trips` 添加用户归属。

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY,
    username VARCHAR(32) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE user_sessions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id),
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_user_sessions_user_id ON user_sessions(user_id);
CREATE INDEX idx_user_sessions_expires_at ON user_sessions(expires_at);

ALTER TABLE trips ADD COLUMN user_id UUID REFERENCES users(id);
CREATE INDEX idx_trips_user_updated ON trips(user_id, updated_at DESC);
```

- 密码使用 Argon2id，存库的哈希包含随机盐和参数；登录调用库提供的验证函数。密码哈希应在受控线程池中计算，限制并发与重试。使用强随机令牌，服务端存其 SHA-256 摘要；会话随机令牌不同于用户密码，可使用快速哈希。
- 行程查询、列表、历史、锁定地点读取和更新都必须受用户约束。历史通过 `trip_history` 与 `trips` 关联鉴权；不要只在前端限制列表。
- 登出将会话标记撤销；修改密码、禁用用户等未来能力需要撤销相关会话。过期会话定期清理。
- 先以可空字段迁移；备份并由管理员显式选择旧行程归属，未归属行程不可由任意用户读取。确认所有数据归属后再设置 `user_id NOT NULL`。已有续接有效期和历史保留规则不因登录改变。
- 需要正式数据库迁移脚本；现有 `CREATE TABLE IF NOT EXISTS` 不会为已存在的 `trips` 自动添加列。SQLite 测试替身也需同步更新，避免测试与生产隔离逻辑不一致。

## 8. 实施与验收

1. 实现数据库迁移、用户和会话存储；注册/登录/当前用户/CSRF/退出接口及限流。
2. 在依赖层解析当前用户，将用户身份贯穿 Controller、TripService、存储层，覆盖所有版本、锁定和保存路径。
3. 配置 Cookie、Origin 白名单、CSRF 密钥、HTTPS 与 CORS；不得把生产密钥提交到仓库。
4. 前端接入注册、登录、退出、会话过期处理；请求带凭据和 CSRF。账号切换或退出时清除前端缓存的行程 ID、用户数据及 CSRF 值。
5. 使用两个独立账号验证：列表、详情、历史、旧版本恢复和续接均无法跨用户访问；同时验证过期、退出、CSRF、登录限流、并发版本冲突和数据库事务回滚。
6. 对 CSRF、会话生命周期、旧接口兼容和旧数据归属进行评审后再上线；本文暂不包含密码重置、邮箱验证、第三方登录或多用户协作。

## 本地启动与本轮实现

1. 在后端环境安装 `requirements.txt`（新增 `argon2-cffi`）。
2. 配置 `DATABASE_URL`，在后端目录执行 `python scripts/migrate_user_auth.py`。迁移可重复执行，只添加表、列与索引；不会修改旧行程的用户归属。
3. 配置至少32字节随机 `AUTH_CSRF_SECRET`。本机 HTTP 使用 `AUTH_COOKIE_SECURE=false`；生产 HTTPS 保持 `true`。会话有效期7天。Origin 白名单沿用 `CORS_ORIGINS`，默认包含本地5173/3000端口。
4. 重启后端，前端打开 `/login` 完成注册及登录，`/my-trips` 只显示自己的规划。前端 POST 自动发送 Cookie 与 CSRF；退出或账号切换会清除原账号的行程缓存。
5. 登录和注册分别按账号10次/15分钟、来源50次/15分钟计数，包括成功请求。计数保存在 PostgreSQL，可供多个 worker 共用。真实代理地址由服务部署正确配置；不直接信任浏览器自报的转发头。
6. 旧行程仍属于未归属数据，新账号看不到；确认归属后可通过受控数据库迁移赋值。当前不提供让用户自行认领任意旧 trip_id 的接口。

独立测试：设置 `TRAVELOS_TEST_DATABASE_URL` 指向测试可用 PostgreSQL，并设置 `TRAVELOS_LOG_DIR` 到临时目录，再执行 `PYTHONPATH=.:tests python -m unittest test_user_auth test_passwords`。测试创建和销毁独立 schema，不向正式用户和行程表写入模拟数据。

认证响应及用户相关地图不允许公共缓存。数据库所有行程读写都绑定用户 ID；存储构造器不传用户时仅访问未归属记录，供离线迁移与旧测试使用，HTTP 依赖始终传入已认证用户。


## 删除规划

`DELETE /api/v1/trips/{trip_id}`，需登录 Cookie、允许的 Origin 和 `X-CSRF-Token`，无需请求体。
成功返回 HTTP 200：`{"code":0,"message":"success","data":null,"meta":{"schema_version":"1.0"}}`（meta 沿用统一响应模型）。
删除整份规划及全部历史版本，数据库事务内完成；过期规划也可删除。不存在、已删除或属于其他用户的规划统一返回404。
“我的规划”的删除按钮要求二次确认，成功后刷新分页并清除相关本地缓存。删除不可撤销。
