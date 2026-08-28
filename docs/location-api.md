# 实时位置上传 API

## 请求

`POST https://campustoday.zhisz.xyz/api/location/proof`

请求头：

```http
Authorization: Bearer <LOCATION_PROOF_TOKEN>
Content-Type: application/json
```

请求体：

```json
{
  "proof_id": "4e0865ce-10ef-4a6d-8ed3-cbf453af3935",
  "latitude": 28.0,
  "longitude": 115.0,
  "accuracy": 20.0,
  "coordinate_system": "wgs84",
  "observed_at": "2026-08-29T20:30:00+08:00",
  "address": "可选的系统定位地址"
}
```

字段规则：

- `proof_id`：每次测量生成新的 UUID v4；重复值返回 `409`。
- `latitude`、`longitude`：有限数字，范围分别为 `[-90,90]`、`[-180,180]`。
- `accuracy`：Android `Location.getAccuracy()`，单位米；当前上限为 100 米。
- `coordinate_system`：Android `LocationManager` 直接取得的位置填 `wgs84`；已经转换的高德/国内地图坐标填 `gcj02`。服务端统一转换到任务使用的 GCJ‑02。
- `observed_at`：定位对象的实际采集时间，ISO 8601 且必须带时区；最多允许比服务器时间早 300 秒或晚 30 秒。
- `address`：可选，最多 500 字符。最终签到地址优先使用任务接口返回的匹配地点地址。
- 接口不绑定设备 ID。Bearer Token 必须保存在 App 私有存储中，不得写日志或放入 URL。

建议每 2–4 分钟上传一次；不要用定时器生成新时间戳来重复发送旧坐标。收到 `200` 只表示位置证明被接受，不代表已经签到。

## 响应

成功：

```json
{
  "accepted": true,
  "reason": "FRESH_DEVICE_LOCATION",
  "expires_at": "2026-08-29T12:35:00+00:00"
}
```

状态码：

- `200`：位置已接受。
- `400`：JSON、UUID、坐标、坐标系或时间格式错误。
- `401`：Bearer Token 缺失或错误。
- `409`：`proof_id` 已使用。
- `422`：位置过期、来自未来、精度不足或定位功能被禁用。

服务器签到条件：当前处于监控窗口；任务名称匹配查寝；任务自身时间窗口已开放；位置证明尚未过期；转换后的位置处于任务返回的 `signPlaceSelected.radius` 内；该任务没有成功或待确认的提交记录；提交后任务列表确认其不再处于未签到状态。
