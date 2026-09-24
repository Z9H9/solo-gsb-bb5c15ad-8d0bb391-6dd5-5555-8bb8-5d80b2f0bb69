# 管网漏点隔离规划台

在无向供水管网上录入**管段、进水点、漏点、必保供水点**以及**绑定管段的阀门**
（最多 14 个，关闭代价为正整数），系统提供两种规划：

1. **常规规划**：计算使全部漏点与全部进水点断开、同时每个必保供水点仍连通
   至少一个进水点的**最低代价关阀组合**。
2. **单阀失效备援规划**：计划关闭的**任意一枚阀门都可能关不上、保持开启**。
   要求方案在正常执行时、以及每一种单阀失效情形（该阀绑定管段保持通水）下，
   全部漏点均与进水点断开、全部必保点仍连通至少一个进水点；在此前提下最小化
   计划关闭阀门的代价总和。

> 两种规划同代价时，均取“关闭阀门编号按升序排列后的列表”字典序最小的方案。
> 备援规划无解时会区分：**原本就没有常规可行方案**（`no_base_plan`）与
> **仅缺少备援方案**（`no_failover_plan`，常规方案存在但都扛不住单阀失效）。

- 常规规划无解时给出明确原因（不存在进水点 / 必保点本来就断水 / 漏点有切不断的
  无阀通路 / 任何隔离方案都会饿死某个必保点）。

## 技术栈

Python 3.11 · FastAPI · SQLite（标准库 `sqlite3`）· 原生 HTML/CSS/JavaScript
（无前端构建、无框架）。求解方式：枚举全部阀门子集（≤2¹⁴=16384），对每个
子集做 BFS 连通性判定，保证全局最优。备援规划在此基础上预计算每个子集的
常规可行性，方案 `M` 备援可行当且仅当 `M` 与每个 `M\{j}`（阀门 j 关不上）
均常规可行。

## 一键启动

```bash
docker compose up --build -d
```

启动后访问：

| 用途 | 地址 |
| --- | --- |
| 规划台页面（含常规/备援两个求解入口） | http://localhost:8000/ |
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
2. 点 **求解最优关阀方案**（常规）：依次展示
   - 关阀清单（编号升序、各阀代价、总代价、枚举统计）；
   - 各节点隔离后的供水状态（含每个节点仍连通的进水点）；
   - 各管段状态：`通水 / 已关阀 / 停水`。
3. 点 **求解单阀失效备援方案**：从当前编辑的管网直接发起备援求解，展示
   - **备援关阀清单与总代价**（编号升序、各阀绑定管段与代价）；
   - **逐阀失效校验表**：正常执行 + 计划清单中每一枚阀门“关不上、保持开启”
     两种情形下，全部漏点是否与进水点断开、全部必保点是否仍有供水；任一情形
     不通过会列出仍通水的漏点 / 断水的必保点；
   - 正常执行时的节点供水状态与管段状态。
   - 无解时横幅明确区分 **无备援方案（存在常规可行方案）** 与
     **常规规划本身无解**，并展示最低代价常规方案逐阀失效的校验明细。
4. **历史方案**：两种求解都会自动入库，历史表“类型”列区分 `常规 / 单阀备援`，
   可回看任一方案及其管网快照（备援方案含全部逐阀失效校验结果）。
5. **保存输入**把当前编辑内容落库（求解时也会随方案保存快照，无需先保存）。

> 示例管网中漏点 L 的两条通路各只有一枚阀门（V2、V4），常规方案为
> `V2+V4`（代价 7），但任一枚关不上 L 都会复通，故备援求解返回
> `NO_FAILOVER_PLAN`，可直接在页面上看到逐阀失效校验。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/network` | 读取当前管网输入 |
| PUT | `/api/network` | 校验并整体覆盖管网输入 |
| POST | `/api/plan` | 常规求解并保存方案；请求体可空（用已保存管网），也可直接提交管网 |
| POST | `/api/plan-failover` | **单阀失效备援求解**（独立 API）；请求体规则同 `/api/plan`，复用同一套管网结构与输入校验 |
| GET | `/api/plans?limit=20` | 最近方案列表（含 `solver`：`base`/`failover`） |
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

`/api/plan` 只可能返回以上四种代码。`/api/plan-failover` 除复用以上代码外，
还可能返回下表代码，并用 `solver:"failover"` 与 `infeasible_kind` 区分两类无解：

| reason_code | infeasible_kind | 含义 |
| --- | --- | --- |
| `NO_INLET` / `GUARANTEED_ALREADY_DISCONNECTED` / `UNCUTTABLE_LEAK_PATH` / `GUARANTEED_CONFLICT` | `no_base_plan` | **原本就没有常规可行方案**（与常规规划同因） |
| `NO_FAILOVER_PLAN` | `no_failover_plan` | **仅缺少备援方案**：存在常规可行方案，但没有任何方案能承受任意单阀关不上 |

### 备援求解请求示例

请求体格式与 `/api/plan` 完全一致。例如漏点唯一通路上有两枚串联阀（任一失效，
另一枚仍可切断漏点）：

```bash
curl -X POST http://localhost:8000/api/plan-failover \
  -H 'Content-Type: application/json' \
  -d '{
    "nodes": [
      {"id":"S","role":"inlet"},
      {"id":"M","role":"normal"},
      {"id":"L","role":"leak"},
      {"id":"H","role":"must_keep"}
    ],
    "segments": [
      {"id":"a","endpoint_a":"S","endpoint_b":"M"},
      {"id":"b","endpoint_a":"M","endpoint_b":"L"},
      {"id":"h","endpoint_a":"S","endpoint_b":"H"}
    ],
    "valves": [
      {"id":"V1","segment_id":"a","cost":1},
      {"id":"V2","segment_id":"b","cost":2}
    ]
  }'
```

### 备援可行响应（节选）

```json
{
  "feasible": true,
  "solver": "failover",
  "total_cost": 3,
  "closed_valves": ["V1", "V2"],
  "subset_count": 4,
  "isolating_subset_count": 3,
  "base_feasible_subset_count": 3,
  "failover_candidate_count": 1,
  "normal_check": {
    "scenario": "normal",
    "failed_valve": null,
    "effective_closed_valves": ["V1", "V2"],
    "leaks_isolated": true, "leaks_reachable": [],
    "keeps_supplied": true, "keeps_starved": [],
    "ok": true
  },
  "failure_checks": [
    {
      "scenario": "valve_failure",
      "failed_valve": "V1",
      "segment_id": "a",
      "cost": 1,
      "effective_closed_valves": ["V2"],
      "leaks_isolated": true, "leaks_reachable": [],
      "keeps_supplied": true, "keeps_starved": [],
      "ok": true
    },
    {
      "scenario": "valve_failure",
      "failed_valve": "V2",
      "segment_id": "b",
      "cost": 2,
      "effective_closed_valves": ["V1"],
      "leaks_isolated": true, "leaks_reachable": [],
      "keeps_supplied": true, "keeps_starved": [],
      "ok": true
    }
  ],
  "node_status": ["…同常规规划（正常执行视角）…"],
  "segment_status": ["…同常规规划（正常执行视角）…"]
}
```

`failure_checks` 对计划清单中的**每一枚阀门**给出一行：该阀关不上、其绑定管段
保持通水时的实际关阀集合、仍可达进水点的漏点（`leaks_reachable`）、断水的
必保点（`keeps_starved`）以及本行是否通过（`ok`）。

### 备援无解响应（仅缺少备援方案）

`feasible:false`、`reason_code:"NO_FAILOVER_PLAN"`、
`infeasible_kind:"no_failover_plan"`；`reason_detail.best_base_plan` 给出最低代价
常规方案（编号升序、总代价）及其逐阀失效校验：

```json
{
  "feasible": false,
  "solver": "failover",
  "reason_code": "NO_FAILOVER_PLAN",
  "infeasible_kind": "no_failover_plan",
  "reason_detail": {
    "base_feasible_subset_count": 1,
    "best_base_plan": {
      "closed_valves": ["V2", "V4"],
      "total_cost": 7,
      "failure_checks": ["…每个计划阀门一行, 结构同上…"]
    }
  }
}
```

## 建模约定

- 管段为无向边；每条管段最多绑定 1 个阀门，关阀等价于从图中删除该管段。
- 节点四种角色互斥；漏点本身隔离后为“已隔离”，与漏点处于同一无水分量的
  普通点显示为“断水”。
- 多个漏点必须**全部**与所有进水点断开才算隔离成功。
- **单阀失效备援**：计划关阀集合 M 中每一枚阀门都可能“关不上、保持开启”，
  失效时实际关阀集合为 M 去掉该阀（其余阀门照常关闭）。备援可行要求 M 本身
  及每个 M\\{j} 都满足“漏点全隔离 + 必保点全有水”。失效的仅是“关闭动作”，
  因此计划清单之外的阀门不受影响；不关闭任何阀门的方案天然备援可行。
- 备援规划与常规规划**目标一致**：仅在可行解集合上不同，优化目标仍是计划
  关闭阀门的代价总和最小，同代价按编号升序列表的字典序取最小。
