# 博物馆展厅灯光编排服务（Museum Lighting Orchestrator）

为博物馆展厅编排可定时切换的灯光场景：注册灯具通道、创建普通/紧急场景，
服务在校验通过后保存编排，并按时间顺序合成可执行时间线。紧急场景可抢占
普通场景，结束后仅恢复到抢占时刻仍有效的场景；任何冲突都会带具体通道、
时间区间和原因返回，且**无效编排不会被保存**。

## 快速开始

### Docker Compose（推荐）

```bash
docker compose up --build
# 服务监听 http://localhost:8000 ，交互式文档见 http://localhost:8000/docs
```

### 本地运行

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 运行测试

```bash
pip install -r requirements-dev.txt
pytest -q
```

## 概念模型

**灯具（Lamp）**：一个可调光通道及其亮度约束。

| 字段 | 说明 |
| --- | --- |
| `channel` | 通道标识，全局唯一，场景通过它引用灯具 |
| `min_brightness` / `max_brightness` | 允许亮度范围（0–100） |
| `default_brightness` | 无场景覆盖时的回落亮度（紧急恢复失败时使用） |

**场景（Scene）**：一组通道在某一时刻的亮度目标与渐变时长。

| 字段 | 说明 |
| --- | --- |
| `kind` | `normal`（普通）或 `emergency`（紧急） |
| `start` | 生效时间，ISO 8601 且**必须带时区**（内部归一化为 UTC） |
| `end` | 失效时间；紧急场景必填，普通场景可选（省略表示长期有效） |
| `channels[]` | `channel` + `brightness` + `fade_seconds`（渐变时长，秒） |

通道 `ch` 的**渐变区间**定义为 `[start, start + fade_seconds]`。

## 校验规则（违反 → 409，且不保存任何数据）

| reason | 含义 |
| --- | --- |
| `lamp_not_found` | 场景引用的通道未注册 |
| `brightness_out_of_range` | 亮度超出该灯具允许范围 |
| `fade_interval_overlap` | 同一通道的渐变区间互相重叠（同类场景之间检查；普通与紧急之间的交叠由抢占语义解决，不算冲突） |
| `emergency_window_overlap` | 同一通道上两个紧急场景的生效窗口重叠 |

请求格式错误（缺时区、`end <= start`、紧急场景缺 `end`、同场景内通道重复等）返回 422。

冲突响应示例（HTTP 409）：

```json
{
  "error": "scene_rejected",
  "conflicts": [
    {
      "channel": "ch-1",
      "reason": "fade_interval_overlap",
      "detail": "fade [2026-10-06T01:00:10+00:00, ...] overlaps scene '日间常设' (...) fade [...] on channel 'ch-1'",
      "interval": { "start": "2026-10-06T01:00:10Z", "end": "2026-10-06T01:00:30Z" }
    }
  ]
}
```

## 时间线合成与紧急抢占语义

`GET /timeline` 把当前全部有效编排合成为按时间排序的可执行指令序列：

- **scene**：普通场景在其 `start` 发出渐变指令；灯具保持该亮度直到下一条指令。
- **emergency**：紧急场景在窗口起点对其声明的通道发出渐变指令；窗口内
  这些通道上的普通指令被丢弃（未声明的通道不受影响）。
- **restore**：紧急窗口结束时，恢复到**抢占时刻仍有效的场景**的亮度
  （该场景无 `end` 或 `end` 晚于紧急结束时间）。若抢占前的场景届时已
  失效，则回落到灯具 `default_brightness`——绝不会恢复到紧急期间
  “插队”出现的场景。恢复渐变沿用紧急场景的 `fade_seconds`。

响应同时给出 `preemptions` 列表，记录每次抢占的通道、窗口、被抢占场景
与恢复目标，便于审计。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| POST | `/lamps` | 注册灯具（通道重复 → 409） |
| GET | `/lamps` / `/lamps/{id}` | 查询灯具 |
| DELETE | `/lamps/{id}` | 删除灯具（被场景引用 → 409） |
| POST | `/scenes` | 创建场景（校验失败 → 409，不保存） |
| GET | `/scenes?kind=normal` | 查询场景（可按类型过滤） |
| GET | `/scenes/{id}` | 查询单个场景 |
| DELETE | `/scenes/{id}` | 删除场景 |
| GET | `/timeline?from=&to=` | 合成时间线（可选时间窗，需带时区） |

## 调用示例（curl）

```bash
BASE=http://localhost:8000

# 1. 注册两个灯具通道
curl -s -X POST $BASE/lamps -H 'Content-Type: application/json' -d '{
  "name": "射灯 A", "channel": "ch-1",
  "min_brightness": 0, "max_brightness": 100, "default_brightness": 10
}'
curl -s -X POST $BASE/lamps -H 'Content-Type: application/json' -d '{
  "name": "洗墙灯 B", "channel": "ch-2",
  "min_brightness": 20, "max_brightness": 80, "default_brightness": 20
}'

# 2. 创建普通场景：09:00 起，ch-1 用 30 秒渐变到 60%
curl -s -X POST $BASE/scenes -H 'Content-Type: application/json' -d '{
  "name": "日间常设", "kind": "normal",
  "start": "2026-10-06T09:00:00+08:00",
  "channels": [{"channel": "ch-1", "brightness": 60, "fade_seconds": 30}]
}'

# 3. 冲突示例：ch-1 的渐变区间与上面重叠 → 409，且不保存
curl -s -X POST $BASE/scenes -H 'Content-Type: application/json' -d '{
  "name": "冲突场景", "start": "2026-10-06T09:00:10+08:00",
  "channels": [{"channel": "ch-1", "brightness": 50, "fade_seconds": 60}]
}'

# 4. 创建紧急场景：10:00–10:15 抢占 ch-1 到 100%
curl -s -X POST $BASE/scenes -H 'Content-Type: application/json' -d '{
  "name": "消防演练", "kind": "emergency",
  "start": "2026-10-06T10:00:00+08:00", "end": "2026-10-06T10:15:00+08:00",
  "channels": [{"channel": "ch-1", "brightness": 100, "fade_seconds": 5}]
}'

# 5. 合成时间线：scene(60) → emergency(100) → restore(60)
curl -s "$BASE/timeline" | python3 -m json.tool

# 6. 只取某一时间窗内的指令
curl -s "$BASE/timeline?from=2026-10-06T09:30:00%2B08:00&to=2026-10-06T11:00:00%2B08:00"
```

第 5 步输出（节选）：

```json
{
  "entries": [
    {"time": "2026-10-06T01:00:00Z", "channel": "ch-1", "brightness": 60,  "fade_seconds": 30, "kind": "scene",     "scene_name": "日间常设"},
    {"time": "2026-10-06T02:00:00Z", "channel": "ch-1", "brightness": 100, "fade_seconds": 5,  "kind": "emergency", "scene_name": "消防演练"},
    {"time": "2026-10-06T02:15:00Z", "channel": "ch-1", "brightness": 60,  "fade_seconds": 5,  "kind": "restore",   "scene_name": "日间常设"}
  ],
  "preemptions": [
    {"emergency_scene_name": "消防演练", "channel": "ch-1",
     "window": {"start": "2026-10-06T02:00:00Z", "end": "2026-10-06T02:15:00Z"},
     "restored_brightness": 60}
  ]
}
```

## 项目结构与说明

```
app/
  main.py        # FastAPI 路由
  models.py      # Pydantic 模型（请求/响应/冲突）
  validation.py  # 业务校验：灯具存在、亮度范围、渐变区间重叠、紧急窗口重叠
  timeline.py    # 时间线合成：普通调度 + 紧急抢占 + 安全恢复
  store.py       # 内存仓储
tests/           # pytest（24 个用例：校验、冲突报告、抢占/恢复语义）
Dockerfile       # python:3.12-slim 单阶段构建
docker-compose.yml
```

**已知限制**：编排数据保存在进程内存中，重启即清空；多副本部署需自行
替换 `app/store.py` 为共享存储（接口已隔离，便于替换）。
