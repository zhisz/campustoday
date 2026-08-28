# campustoday

今日校园晚查寝自动化服务。提供受密码保护的管理后台、SQLite 审计数据、后台调度、可信设备位置证明与校园 geofence。江西理工大学 2026 年任务列表和详情协议已经过实机验证；提交接口的字段构造已从当前前端版本核对，但生产仍默认关闭提交。

## 本地测试

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

## 生产部署

复制 `.env.example` 为 `.env`，设置强随机 `APP_SECRET`、`ADMIN_PASSWORD`、`LOCATION_PROOF_TOKEN`，然后：

```bash
docker compose config
docker compose build
docker compose up -d
curl -fsS http://127.0.0.1:3600/health
```

数据默认保存在 `/data/campustoday.db`（Compose 映射到宿主机 `./data`）。应用只绑定 `127.0.0.1`。

## 可信位置证明

可信设备须上传带时区且足够新鲜的真实定位：

```http
POST /api/location/proof
Authorization: Bearer <LOCATION_PROOF_TOKEN>
Content-Type: application/json

{"latitude": 0, "longitude": 0, "accuracy": 10, "observed_at": "2026-08-29T20:30:00+08:00"}
```

服务器会检查时间新鲜度和地理围栏；失败只记录状态，不会触发提交。严禁使用固定坐标、历史坐标或定位欺骗。

## 协议状态

设置 `CPDAILY_MODE=jxust` 可启用只读任务轮询。登录会话通过 `CPDAILY_SESSION_COOKIE` 提供，不得写入 Git 或日志。提交还需要同时设置 `CPDAILY_SUBMIT_ENABLED=true`、完整可信设备资料和可信设备上传的新鲜位置证明；缺少任何一项都会拒绝提交。

当前适配目标是 `prod_v2.1.2.312_1WtW`。已验证 `getStuAttendacesInOneDay` 与 `detailSignInstance`；`submitSign` 的请求字段已从该版本前端核对，但在本人完成一次合法开放任务的实机验收前不得打开生产提交开关。
