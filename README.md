# campustoday

今日校园晚查寝自动化服务的安全第一版。提供受密码保护的管理后台、SQLite 审计数据、后台调度、可信设备位置证明与校园 geofence。生产默认 `CPDAILY_MODE=disabled`，因为公开协议样本停留在 2021–2022 年；在验证学校 2026 年实际接口前不会提交任何签到。

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

参考项目 `F-19-F/SWU-CpDaily` 最后提交于 2022-01-20。旧版使用 `getStuAttendacesInOneDay`、`detailSignInstance`、`getStuSignInfosByWeekMonth`、`submitSign`，以及 DES/AES/MD5 封装。它们仅保留为研究背景，没有直接复制进生产请求路径。启用真实集成前需要学校入口、获授权账号以及当前 App 的实际请求/响应样本。
