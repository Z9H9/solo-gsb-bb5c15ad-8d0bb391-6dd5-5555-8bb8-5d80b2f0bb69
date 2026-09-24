# 管网漏点隔离规划台

在无向供水管网上录入**管段、进水点、漏点、必保供水点**以及**绑定管段的阀门**
（最多 14 个，关闭代价为正整数），系统提供两类求解：

1. **常规最优关阀方案**

   > 使全部漏点与全部进水点断开、同时每个必保供水点仍连通至少一个进水点的
   > **最低代价关阀组合**。

2. **单阀失效备援方案**（独立 API：`POST /api/plan/failsafe`）

   > 计划关闭的**任意一枚阀门可能关不上并保持开启**。要求在正常执行以及
   > **每一种单阀失效情形**下，全部漏点均与进水点断开、全部必保点仍连通
   > 至少一个进水点，并以计划关闭阀门的代价总和最小为目标。

- 同代价时，取“关闭阀门编号按升序排列后的列表”字典序最小的方案；
- 常规求解无解时给出明确原因（不存在进水点 / 必保点本来就断水 / 漏点有切不断的
  无阀通路 / 任何隔离方案都会饿死某个必保点）；
- 备援求解无解时**区分两类**：常规问题本就无解（沿用常规原因代码），或
  `NO_FAILSAFE_PLAN`（存在常规可行方案，但没有任何方案扛得住全部单阀失效，
  附最便宜的若干被阻断方案及逐阀失效证据）。

## 技术栈

Python 3.11 · FastAPI · SQLite（标准库 `sqlite3`）· 原生 HTML/CSS/JavaScript
（无前端构建、无框架）。求解方式：枚举全部阀门子集（≤2¹⁴=16384），对每个
子集做 BFS 连通性判定；备援求解对每个候选子集再逐枚计划关闭阀门做一次
“该阀保持开启”的 BFS 校验，规模仍为 V·2^V，保证全局最优。

## 一键启动

```bash
docker compose up --build -d
```

启动后访问：

| 用途 | 地址 |
| --- | --- |
| 规划台页面 | http://localhost:8000/ |
| API 健康检查 | http://localhost:8000/api/health |
| OpenAPI 文档 | http://localhost:8000/docs |

容器**首次启动自动建表**，并在空库中写入一份示例管网（页面上点
「载入示例管网」可随时恢复）。停止：`docker compose down`；连同数据一起
删除：`docker compose down -v`。

## 配置

| 配置项 | 方式 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 服务端口 | compose 的 `ports`，如 `"8080:8000"` | `8000:8000` | 宿主机端口可改，容器内固定 8000 |
| SQLite 文件 | 环境变量 `DB_PATH`（Dockerfile 已设） | `/data/pipeline.db` | `/data` 已挂载为命名卷 `pipeline-data` |

## 本地（无 Docker）运行

```bash
pip install -r requirements.txt
DB_PATH=./pipeline.db uvicorn main:app --host 0.0.0.0 --port 8000 --app-dir app
```
首次运行同样会自动初始化数据库并写入示例数据。

## 页面用法

1. **管网输入**：三张表分别维护节点（选角色：进水点/漏点/必保供水点/普通点）、
   无向管段（选两个端点）、阀门（选一条绑定管段并填正整数代价，≤14 个）。
   下方 Canvas 自动绘制力导向管网示意，红色虚线方块表示被关闭的阀门。
2. 点 **求解最优关阀方案**：依次展示
   - 关阀清单（编号升序、各阀代价、总代价、枚举统计）；
   - 各节点隔离后的供水状态（含每个节点仍连通的进水点）；
   - 各管段状态：`通水 / 已关阀 / 停水`。
3. 点 **求解单阀失效备援方案**：从当前编辑的管网发起独立备援求解，展示
   - 备援关阀清单（编号升序、各阀代价、总代价）；
   - **正常执行与逐阀失效校验表**：逐枚列出计划关闭的阀门“关不上”时是否
     仍合格，失败时给出重新通水的漏点、断水的必保点及证据通路；
   - 正常执行情形下的节点供水状态与管段状态（同步绘制到管网示意）。
   无解时明确区分“常规问题本就无解”与“仅缺少备援方案”，后者附最便宜的
   若干常规可行方案及其被哪种单阀失效阻断。备援求解结果不写入历史库。
4. **历史方案**：每次常规求解自动入库，可回看任一方案及其管网快照。
5. **保存输入**把当前编辑内容落库（求解时也会随方案保存快照，无需先保存）。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/network` | 读取当前管网输入 |
| PUT | `/api/network` | 校验并整体覆盖管网输入 |
| POST | `/api/plan` | 求解并保存方案；请求体可空（用已保存管网），也可直接提交管网 |
| POST | `/api/plan/failsafe` | **单阀失效备援求解**（独立、不入库）；请求体规则同 `/api/plan` |
| GET | `/api/plans?limit=20` | 最近方案列表 |
| GET | `/api/plans/{id}` | 方案详情（含节点/管段状态与管网快照） |
| POST | `/api/demo` | 恢复内置示例管网 |
| POST | `/api/clear` | 清空管网输入 |

### 请求示例

```bash
curl -X POST http://localhost:8000/api/plan \
  -H 'Content-Type: application/json' \
  -d '{
    "nodes": [
      {"id":"S","role":"inlet"},
      {"id":"L","role":"leak"},
      {"id":"H","role":"must_keep"},
      {"id":"A","role":"normal"}
    ],
    "segments": [
      {"id":"p1","endpoint_a":"S","endpoint_b":"A"},
      {"id":"p2","endpoint_a":"A","endpoint_b":"L"},
      {"id":"p3","endpoint_a":"A","endpoint_b":"H"}
    ],
    "valves": [
      {"id":"V1","segment_id":"p2","cost":2}
    ]
  }'
```

### 可行响应（节选）

```json
{
  "feasible": true,
  "total_cost": 2,
  "closed_valves": ["V1"],
  "subset_count": 2,
  "isolating_subset_count": 1,
  "node_status": [
    {"id":"S","role":"inlet","supplied":true,"status":"inlet_supply","connected_inlets":["S"]},
    {"id":"H","role":"must_keep","supplied":true,"status":"supplied","connected_inlets":["S"]},
    {"id":"L","role":"leak","supplied":false,"status":"isolated","connected_inlets":[]}
  ],
  "segment_status": [
    {"id":"p2","status":"closed","flowing":false}
  ]
}
```

### 无解响应

`feasible:false`，`reason_code` 取值：

| reason_code | 含义 |
| --- | --- |
| `NO_INLET` | 管网没有进水点 |
| `GUARANTEED_ALREADY_DISCONNECTED` | 一个阀门都不关，已有必保点不通任何进水点 |
| `UNCUTTABLE_LEAK_PATH` | 漏点经**不含任何阀门**的管段连到进水点，无法切断（附一条证据通路） |
| `GUARANTEED_CONFLICT` | 每个能隔离漏点的组合都会使某个必保点失去全部进水点（附在所有方案中都断水的必保点及示例组合） |
| `NO_FAILSAFE_PLAN` | **仅备援求解返回**：常规方案可行，但每个常规可行组合都有扛不住的单阀失效情形（附 `cheapest_blocked_plans`） |

### 备援求解请求与响应（`POST /api/plan/failsafe`）

请求体与 `/api/plan` 完全相同（复用同一套管网结构与输入校验），可空。
可行响应（节选）：

```json
{
  "feasible": true,
  "failsafe": true,
  "plan_type": "failsafe",
  "total_cost": 2,
  "closed_valves": ["V1", "V2"],
  "subset_count": 4,
  "regular_feasible_subset_count": 1,
  "failsafe_feasible_subset_count": 1,
  "failure_scenario_count": 3,
  "normal_execution": {"ok": true, "leak_reachable": [], "starved_keeps": []},
  "failure_checks": [
    {
      "failed_valve": "V1",
      "ok": true,
      "leak_reachable": [],
      "starved_keeps": [],
      "witness_path": "",
      "effective_closed_valves": ["V2"]
    }
  ]
}
```

`failure_checks` 按计划关闭阀门的编号升序排列，逐枚模拟“该阀关不上、
保持开启”；校验失败时 `ok:false`，并在 `leak_reachable` /
`starved_keeps` / `witness_path` 中给出证据。无需关阀即合格时
`failure_checks` 为空、`failure_scenario_count` 为 1（仅正常执行）。

`NO_FAILSAFE_PLAN` 响应的 `reason_detail.cheapest_blocked_plans` 按代价
升序列出最多 10 个常规可行但不具备备援的方案，每个方案附
`blocking_failures`（结构同 `failure_checks` 中的失败条目）。

## 建模约定

- 管段为无向边；每条管段最多绑定 1 个阀门，关阀等价于从图中删除该管段。
- 节点四种角色互斥；漏点本身隔离后为“已隔离”，与漏点处于同一无水分量的
  普通点显示为“断水”。
- 多个漏点必须**全部**与所有进水点断开才算隔离成功。
- 备援语义：仅枚举**计划关闭**的阀门的单阀失效；计划外阀门本来就保持开启，
  不存在“失效”。失效时实际关阀集合 = 计划集合去掉该阀。
