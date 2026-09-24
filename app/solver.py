"""管网漏点隔离求解器。

模型:
  - 节点 = 无向管网上的点; 每个节点有角色: inlet(进水点)/leak(漏点)/
    must_keep(必保供水点)/normal(普通点)。
  - 管段 = 无向边; 每条管段最多绑定 1 个阀门。关闭阀门即删除对应管段。
  - 目标: 选择代价之和最小的阀门组合, 使
      (A) 每个漏点与所有进水点都不连通(漏点被隔离);
      (B) 每个必保点仍与至少一个进水点连通。
    同代价时, 取关闭阀门编号升序表的字典序最小者。

阀门最多 14 个, 枚举全部 2^V 个子集(V<=14 => 16384), 对每个子集做
BFS 连通性检查即可, 简单且确定。

另有单阀失效备援求解 solve_failsafe(): 在常规要求之上, 进一步要求计划
关闭的每一枚阀门单独"关不上(保持开启)"时, 漏点仍全部隔离、必保点仍
全部有供水; 同样枚举全部子集即可判定。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

MAX_VALVES = 14

# 无备援方案时, 返回的"最便宜但扛不住单阀失效"的常规方案示例数
_BLOCKED_EXAMPLE_LIMIT = 10


class NetworkValidationError(ValueError):
    """输入管网数据不合法, errors 字段携带逐条原因。"""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _norm_id(raw: Any) -> str:
    return str(raw if raw is not None else "").strip()


def validate_network(payload: dict[str, Any]) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    """校验并规整 {nodes, segments, valves}。

    返回 (nodes, segments, valves), 均为带规整字段的 dict 列表。
    """
    errors: list[str] = []

    raw_nodes = payload.get("nodes") or []
    raw_segments = payload.get("segments") or []
    raw_valves = payload.get("valves") or []
    if not isinstance(raw_nodes, list) or not isinstance(raw_segments, list) \
            or not isinstance(raw_valves, list):
        raise NetworkValidationError(["nodes/segments/valves 必须是数组"])

    # ---- 节点 ----
    node_role = {"inlet", "leak", "must_keep", "normal"}
    nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for i, nd in enumerate(raw_nodes):
        if not isinstance(nd, dict):
            errors.append(f"节点 #{i + 1}: 必须是对象")
            continue
        nid = _norm_id(nd.get("id"))
        if not nid:
            errors.append(f"节点 #{i + 1}: 缺少编号")
        elif nid in node_ids:
            errors.append(f"节点编号重复: {nid}")
        role = nd.get("role", "normal")
        if role not in node_role:
            errors.append(f"节点 {nid or i + 1}: 角色非法({role}), "
                          f"应为 inlet/leak/must_keep/normal")
        label = _norm_id(nd.get("label")) or nid
        node_ids.add(nid)
        nodes.append({"id": nid, "role": role, "label": label})

    # ---- 阀门 ----
    valves: list[dict[str, Any]] = []
    valve_ids: set[str] = set()
    for i, vl in enumerate(raw_valves):
        if not isinstance(vl, dict):
            errors.append(f"阀门 #{i + 1}: 必须是对象")
            continue
        vid = _norm_id(vl.get("id"))
        if not vid:
            errors.append(f"阀门 #{i + 1}: 缺少编号")
        elif vid in valve_ids:
            errors.append(f"阀门编号重复: {vid}")
        cost = vl.get("cost")
        try:
            cost_int = int(cost)
            if cost_int <= 0 or str(cost_int) != str(cost).strip():
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"阀门 {vid or i + 1}: 关闭代价必须是正整数")
            cost_int = None
        seg_id = _norm_id(vl.get("segment_id"))
        if not seg_id:
            errors.append(f"阀门 {vid or i + 1}: 必须绑定一条管段")
        valve_ids.add(vid)
        valves.append({"id": vid, "cost": cost_int, "segment_id": seg_id})

    if len(valves) > MAX_VALVES:
        errors.append(f"阀门数量为 {len(valves)}, 超过上限 {MAX_VALVES} 个")

    # ---- 管段 ----
    segments: list[dict[str, Any]] = []
    segment_ids: set[str] = set()
    seg_valves: dict[str, str] = {}
    for vl in valves:
        if vl["segment_id"]:
            if vl["segment_id"] in seg_valves:
                errors.append(
                    f"管段 {vl['segment_id']} 同时绑定了阀门 "
                    f"{seg_valves[vl['segment_id']]} 和 {vl['id']}"
                    f"(每条管段最多 1 个阀门)"
                )
            else:
                seg_valves[vl["segment_id"]] = vl["id"]

    for i, sg in enumerate(raw_segments):
        if not isinstance(sg, dict):
            errors.append(f"管段 #{i + 1}: 必须是对象")
            continue
        sid = _norm_id(sg.get("id"))
        if not sid:
            errors.append(f"管段 #{i + 1}: 缺少编号")
        elif sid in segment_ids:
            errors.append(f"管段编号重复: {sid}")
        a, b = _norm_id(sg.get("endpoint_a")), _norm_id(sg.get("endpoint_b"))
        if a not in node_ids:
            errors.append(f"管段 {sid or i + 1}: 端点 {a or '(空)'} 不存在")
        if b not in node_ids:
            errors.append(f"管段 {sid or i + 1}: 端点 {b or '(空)'} 不存在")
        if a and a == b:
            errors.append(f"管段 {sid or i + 1}: 两个端点不能相同({a})")
        segment_ids.add(sid)
        segments.append({
            "id": sid,
            "endpoint_a": a,
            "endpoint_b": b,
            "valve_id": seg_valves.get(sid),
        })

    for vl in valves:
        if vl["segment_id"] and vl["segment_id"] not in segment_ids:
            errors.append(f"阀门 {vl['id']}: 绑定的管段 {vl['segment_id']} 不存在")

    if errors:
        raise NetworkValidationError(errors)
    return nodes, segments, valves


@dataclass
class _Graph:
    """校验后管网上下文: 编号映射、邻接表、阀门位序与连通性 BFS。"""

    nodes: list[dict[str, Any]]
    segments: list[dict[str, Any]]
    valves_sorted: list[dict[str, Any]]
    costs: list[int]
    n: int
    nv: int
    idx: dict[str, int]
    inlets: set[int]
    leaks: set[int]
    keeps: set[int]
    leak_bits: int
    keep_bits: int
    adj: list[list[tuple[int, int]]]
    edge_closed_bit: list[int]

    def reach(self, mask: int, sources: set[int]) -> int:
        """从 sources 出发、避开 mask 关阀管段后可达节点的位掩码。"""
        seen = 0
        dq: deque[int] = deque()
        for s in sources:
            if not (seen >> s) & 1:
                seen |= 1 << s
                dq.append(s)
        while dq:
            u = dq.popleft()
            for w, e in self.adj[u]:
                bit = self.edge_closed_bit[e]
                if bit and (mask & bit):
                    continue
                if not (seen >> w) & 1:
                    seen |= 1 << w
                    dq.append(w)
        return seen

    def mask_ok(self, mask: int) -> bool:
        """mask 关阀方案是否同时满足漏点全隔离、必保点全供水。"""
        fed = self.reach(mask, self.inlets)
        return (fed & self.leak_bits) == 0 and (fed & self.keep_bits) == self.keep_bits


def _build_graph(
    nodes: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    valves: list[dict[str, Any]],
) -> _Graph:
    ids = [nd["id"] for nd in nodes]
    idx = {nid: i for i, nid in enumerate(ids)}
    n = len(ids)

    inlets = {i for i, nd in enumerate(nodes) if nd["role"] == "inlet"}
    leaks = {i for i, nd in enumerate(nodes) if nd["role"] == "leak"}
    keeps = {i for i, nd in enumerate(nodes) if nd["role"] == "must_keep"}

    # 阀门按编号升序: 保证位序确定, 同代价时用编号列表直接做字典序比较
    valves_sorted = sorted(valves, key=lambda vl: vl["id"])
    v_idx = {vl["id"]: i for i, vl in enumerate(valves_sorted)}
    costs = [vl["cost"] for vl in valves_sorted]
    nv = len(valves_sorted)

    # 邻接表: adj[u] = [(v, edge_index)]; 边序与 segments 一致。
    # edge_closed_bit[k] = 该边阀门的位(无阀门边为 0)。
    adj: list[list[tuple[int, int]]] = [[] for _ in range(n)]
    edge_closed_bit: list[int] = []
    for e, sg in enumerate(segments):
        u, v = idx[sg["endpoint_a"]], idx[sg["endpoint_b"]]
        bit = (1 << v_idx[sg["valve_id"]]) if sg["valve_id"] is not None else 0
        edge_closed_bit.append(bit)
        adj[u].append((v, e))
        adj[v].append((u, e))

    return _Graph(
        nodes=nodes, segments=segments, valves_sorted=valves_sorted,
        costs=costs, n=n, nv=nv, idx=idx, inlets=inlets, leaks=leaks,
        keeps=keeps,
        leak_bits=sum(1 << x for x in leaks),
        keep_bits=sum(1 << x for x in keeps),
        adj=adj, edge_closed_bit=edge_closed_bit,
    )


def _subset_costs(g: _Graph) -> list[int]:
    """全部阀门子集的代价表; 按 lowbit DP 递推。"""
    total = 1 << g.nv
    subset_cost = [0] * total
    for m in range(1, total):
        low = m & -m
        subset_cost[m] = (subset_cost[m ^ low]
                          + g.costs[low.bit_length() - 1])
    return subset_cost


def _mask_valve_ids(g: _Graph, mask: int) -> list[str]:
    return [g.valves_sorted[b]["id"] for b in range(g.nv) if (mask >> b) & 1]


def solve(payload: dict[str, Any]) -> dict[str, Any]:
    """求解最低代价关阀组合, 返回规划结果(见 README 的返回示例)。"""
    nodes, segments, valves = validate_network(payload)
    g = _build_graph(nodes, segments, valves)
    total_masks = 1 << g.nv

    # ---- 前置检查(不关任何阀门时) ----
    if not g.inlets:
        return _infeasible("NO_INLET", "管网中没有进水点, 任何方案都无法供水。")
    base_reach = g.reach(0, g.inlets)
    already_off = g.keep_bits & ~base_reach
    if already_off:
        names = [nodes[i]["id"] for i in range(g.n) if (already_off >> i) & 1]
        return _infeasible(
            "GUARANTEED_ALREADY_DISCONNECTED",
            "即使不关任何阀门, 以下必保点也无法连通任何进水点(关阀只会进一步"
            "减少连通性, 故无解): " + ", ".join(names),
            {"starved_guaranteed": names},
        )

    isolating_count = 0
    if not g.leaks:
        # 没有漏点: 无需关阀即可满足全部要求
        best_mask = 0
        isolating_count = 1
    else:
        # ---- 枚举所有阀门子集 ----
        subset_cost = _subset_costs(g)

        best_mask = -1
        best_cost = None
        best_list: tuple[str, ...] = ()

        for m in range(total_masks):
            fed = g.reach(m, g.inlets)
            if (fed & g.leak_bits) == 0:
                isolating_count += 1
            else:
                continue
            if (fed & g.keep_bits) != g.keep_bits:
                continue
            closed = tuple(_mask_valve_ids(g, m))
            c = subset_cost[m]
            if (best_cost is None or c < best_cost
                    or (c == best_cost and closed < best_list)):
                best_cost, best_mask, best_list = c, m, closed

        if best_mask < 0:
            return _explain_infeasible(g, isolating_count)

    # ---- 构造最优方案下的节点/管段状态 ----
    node_status, segment_status = _build_statuses(g, best_mask)

    closed = _mask_valve_ids(g, best_mask)
    total_cost = sum(g.costs[b] for b in range(g.nv)
                     if (best_mask >> b) & 1)
    return {
        "feasible": True,
        "total_cost": total_cost,
        "closed_valves": closed,
        "closed_count": len(closed),
        "subset_count": total_masks,
        "isolating_subset_count": isolating_count,
        "node_status": node_status,
        "segment_status": segment_status,
        "message": (f"已找到最优关阀方案: 关闭 {len(closed)} 个阀门, "
                    f"总代价 {total_cost}。"),
    }


def solve_failsafe(payload: dict[str, Any]) -> dict[str, Any]:
    """求解单阀失效备援关阀组合。

    在常规约束之上, 要求计划关闭的每一枚阀门单独失效(关不上、保持开启)
    时, 仍满足: 全部漏点与进水点断开、全部必保点连通至少一个进水点。
    计划外的阀门本来就保持开启, 不在失效情形枚举范围内。

    无解区分:
      - 常规问题本身无解: 复用 solve() 的 reason_code;
      - 存在常规可行方案但没有任何方案扛得住全部单阀失效:
        NO_FAILSAFE_PLAN(附最便宜的若干被阻断方案及其失效证据)。
    """
    nodes, segments, valves = validate_network(payload)
    g = _build_graph(nodes, segments, valves)
    total_masks = 1 << g.nv

    # ---- 前置检查与常规求解完全一致 ----
    if not g.inlets:
        return _mark_failsafe(_infeasible(
            "NO_INLET", "管网中没有进水点, 任何方案都无法供水。"))
    base_reach = g.reach(0, g.inlets)
    already_off = g.keep_bits & ~base_reach
    if already_off:
        names = [nodes[i]["id"] for i in range(g.n) if (already_off >> i) & 1]
        return _mark_failsafe(_infeasible(
            "GUARANTEED_ALREADY_DISCONNECTED",
            "即使不关任何阀门, 以下必保点也无法连通任何进水点(关阀只会进一步"
            "减少连通性, 故无解): " + ", ".join(names),
            {"starved_guaranteed": names},
        ))

    subset_cost = _subset_costs(g)

    best_mask = -1
    best_cost = None
    best_list: tuple[str, ...] = ()
    regular_count = 0
    isolating_count = 0
    robust_count = 0
    blocked: list[tuple[int, tuple[str, ...], int]] = []

    for m in range(total_masks):
        fed = g.reach(m, g.inlets)
        if (fed & g.leak_bits) == 0:
            isolating_count += 1
        if not ((fed & g.leak_bits) == 0
                and (fed & g.keep_bits) == g.keep_bits):
            continue
        regular_count += 1

        _, all_ok = _failure_checks(g, m)
        if all_ok:
            robust_count += 1
            closed = tuple(_mask_valve_ids(g, m))
            c = subset_cost[m]
            if (best_cost is None or c < best_cost
                    or (c == best_cost and closed < best_list)):
                best_cost, best_mask, best_list = c, m, closed
        else:
            blocked.append((subset_cost[m], tuple(_mask_valve_ids(g, m)), m))

    if best_mask < 0:
        if regular_count == 0:
            # 常规问题本身就无解, 直接复用常规无解解释
            return _mark_failsafe(_explain_infeasible(g, isolating_count))
        # 有常规可行方案, 但每个方案都有扛不住的单阀失效情形
        blocked.sort(key=lambda x: (x[0], x[1]))
        examples = []
        for c, closed_tuple, m in blocked[:_BLOCKED_EXAMPLE_LIMIT]:
            checks, _ = _failure_checks(g, m)
            examples.append({
                "closed_valves": list(closed_tuple),
                "total_cost": c,
                "blocking_failures": [v for v in checks if not v["ok"]],
            })
        return _mark_failsafe(_infeasible(
            "NO_FAILSAFE_PLAN",
            f"存在 {regular_count} 个常规可行的关阀组合, 但每一个组合中都至少"
            "有一枚阀门一旦关不上(保持开启), 就会导致漏点重新连通进水点或"
            "必保点失去全部供水, 因此不存在满足单阀失效备援要求的方案。",
            {
                "regular_feasible_subset_count": regular_count,
                "cheapest_blocked_plans": examples,
            },
        ), regular_count=regular_count)

    # ---- 成功: 汇总正常执行 + 逐阀失效校验 ----
    failure_checks, _ = _failure_checks(g, best_mask)
    normal = _scenario(g, best_mask)
    node_status, segment_status = _build_statuses(g, best_mask)

    closed = _mask_valve_ids(g, best_mask)
    total_cost = sum(g.costs[b] for b in range(g.nv)
                     if (best_mask >> b) & 1)

    if closed:
        message = (
            f"已找到单阀失效备援方案: 关闭 {len(closed)} 个阀门, 总代价 "
            f"{total_cost}。正常执行以及 {len(closed)} 种单阀失效情形"
            f"(共 {len(closed) + 1} 种)下, 全部漏点均与进水点断开、"
            "全部必保点均连通至少一个进水点。"
        )
    else:
        message = ("无需关闭任何阀门即满足全部要求, 不存在单阀失效情形, "
                   "备援约束自然满足。")

    return {
        "feasible": True,
        "failsafe": True,
        "plan_type": "failsafe",
        "total_cost": total_cost,
        "closed_valves": closed,
        "closed_count": len(closed),
        "subset_count": total_masks,
        "regular_feasible_subset_count": regular_count,
        "failsafe_feasible_subset_count": robust_count,
        "failure_scenario_count": len(closed) + 1,
        "normal_execution": {
            "ok": True,
            "leak_reachable": normal["leak_reachable"],
            "starved_keeps": normal["starved_keeps"],
        },
        "failure_checks": failure_checks,
        "node_status": node_status,
        "segment_status": segment_status,
        "message": message,
    }


def _scenario(g: _Graph, mask: int) -> dict[str, Any]:
    """评估某一具体关阀情形(可能有阀门失效后保持开启)的供水结果。"""
    fed = g.reach(mask, g.inlets)
    leak_reachable = [g.nodes[i]["id"] for i in range(g.n)
                      if (g.leak_bits >> i) & 1 and (fed >> i) & 1]
    starved_keeps = [g.nodes[i]["id"] for i in range(g.n)
                     if (g.keep_bits >> i) & 1 and not (fed >> i) & 1]
    witness = ""
    if leak_reachable:
        witness = _witness_path(g, mask, leak_reachable)
    return {
        "ok": not leak_reachable and not starved_keeps,
        "leak_reachable": leak_reachable,
        "starved_keeps": starved_keeps,
        "witness_path": witness,
    }


def _failure_checks(
    g: _Graph, mask: int
) -> tuple[list[dict[str, Any]], bool]:
    """对计划关闭(mask)中的每一枚阀门, 检查它单独保持开启时是否仍合格。

    按阀门编号升序(即位序)返回逐阀校验; 第二项为是否全部通过。
    """
    checks: list[dict[str, Any]] = []
    all_ok = True
    for b in range(g.nv):
        if not ((mask >> b) & 1):
            continue
        vid = g.valves_sorted[b]["id"]
        result = _scenario(g, mask ^ (1 << b))
        checks.append({
            "failed_valve": vid,
            "ok": result["ok"],
            "leak_reachable": result["leak_reachable"],
            "starved_keeps": result["starved_keeps"],
            "witness_path": result["witness_path"],
            "effective_closed_valves": _mask_valve_ids(g, mask ^ (1 << b)),
        })
        all_ok = all_ok and result["ok"]
    return checks, all_ok


def _build_statuses(
    g: _Graph, best_mask: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """构造给定关阀方案下的节点/管段状态(正常执行情形)。"""
    fed = g.reach(best_mask, g.inlets)
    # 每个节点在该方案下可连通的进水点(逐进水点 BFS)
    inlet_of: list[list[str]] = [[] for _ in range(g.n)]
    for j in sorted(g.inlets):
        r = g.reach(best_mask, {j})
        for i in range(g.n):
            if (r >> i) & 1:
                inlet_of[i].append(g.nodes[j]["id"])

    node_status: list[dict[str, Any]] = []
    for i, nd in enumerate(g.nodes):
        is_fed = bool((fed >> i) & 1)
        if nd["role"] == "leak":
            status = "isolated" if not is_fed else "leak_not_isolated"
        elif not is_fed:
            status = "disconnected"
        elif nd["role"] == "inlet":
            status = "inlet_supply"
        else:
            status = "supplied"
        node_status.append({
            "id": nd["id"], "label": nd["label"], "role": nd["role"],
            "supplied": is_fed, "status": status,
            "connected_inlets": inlet_of[i],
        })

    closed_valve_ids = {g.valves_sorted[b]["id"]
                        for b in range(g.nv) if (best_mask >> b) & 1}
    segment_status: list[dict[str, Any]] = []
    for sg in g.segments:
        u, v = g.idx[sg["endpoint_a"]], g.idx[sg["endpoint_b"]]
        if sg["valve_id"] in closed_valve_ids:
            status, flowing = "closed", False
        elif (fed >> u) & 1 and (fed >> v) & 1:
            status, flowing = "flowing", True
        else:
            status, flowing = "dead", False
        segment_status.append({
            "id": sg["id"], "endpoint_a": sg["endpoint_a"],
            "endpoint_b": sg["endpoint_b"], "valve_id": sg["valve_id"],
            "status": status, "flowing": flowing,
        })
    return node_status, segment_status


def _explain_infeasible(g: _Graph, isolating_count: int) -> dict[str, Any]:
    """无解原因分析(已有方案都不满足必保约束)。"""
    if isolating_count == 0:
        # 全关阀后仍有漏点可达进水点: 存在不含阀门的通路
        all_closed = (1 << g.nv) - 1
        fed_all_closed = g.reach(all_closed, g.inlets)
        reachable_leaks = [g.nodes[i]["id"] for i in range(g.n)
                           if ((g.leak_bits >> i) & 1
                               and (fed_all_closed >> i) & 1)]
        witness = _witness_path(g, all_closed, reachable_leaks)
        detail = "关闭全部阀门后, 漏点仍可经不含阀门的管段到达进水点, 无法切断。"
        if witness:
            detail += f" 无阀通路示例: {witness}。"
        return _infeasible(
            "UNCUTTABLE_LEAK_PATH", detail,
            {"reachable_leaks_when_all_closed": reachable_leaks,
             "witness_path": witness},
        )

    # 枚举所有"能隔离漏点"的方案, 求在每一方案中都断水的必保点交集
    always_starved: set[int] = set(g.keeps)
    witness_valves: list[str] = []
    for m in range(1 << g.nv):
        fed = g.reach(m, g.inlets)
        if (fed & g.leak_bits) != 0:
            continue
        starved = {i for i in g.keeps if not (fed >> i) & 1}
        always_starved &= starved
        if not witness_valves and starved:
            witness_valves = _mask_valve_ids(g, m)

    if always_starved:
        names = [g.nodes[i]["id"] for i in sorted(always_starved)]
        return _infeasible(
            "GUARANTEED_CONFLICT",
            "任何能隔离全部漏点的关阀组合, 都会使以下必保点与所有进水点断开, "
            "故不存在可行方案: " + ", ".join(names) + "。",
            {"starved_in_every_isolation": names,
             "example_isolation_valves": witness_valves},
        )
    return _infeasible(
        "GUARANTEED_CONFLICT",
        "每个能隔离漏点的关阀组合都会使至少一个必保点断水(不同组合影响的"
        "必保点不同), 无法同时满足隔离与必保供水要求。",
        {"example_isolation_valves": witness_valves},
    )


def _witness_path(g: _Graph, mask: int, target_node_ids: list[str]) -> str:
    """在关闭 mask 后仍走得通的图里, BFS 重建一条 进水点->目标漏点 的路径。"""
    if not target_node_ids:
        return ""
    targets = {g.idx[t] for t in target_node_ids}
    prev_node = [-1] * g.n
    prev_edge = [-1] * g.n
    dq: deque[int] = deque()
    for s in g.inlets:
        if prev_node[s] == -1:
            prev_node[s] = s
            dq.append(s)
    found = -1
    while dq:
        u = dq.popleft()
        if u in targets:
            found = u
            break
        for w, e in g.adj[u]:
            bit = g.edge_closed_bit[e]
            if bit and (mask & bit):
                continue
            if prev_node[w] == -1:
                prev_node[w] = u
                prev_edge[w] = e
                dq.append(w)
    if found < 0:
        return ""
    path_nodes: list[str] = []
    path_segs: list[str] = []
    cur = found
    while prev_node[cur] != cur:
        path_nodes.append(g.nodes[cur]["id"])
        path_segs.append(g.segments[prev_edge[cur]]["id"])
        cur = prev_node[cur]
    path_nodes.append(g.nodes[cur]["id"])
    path_nodes.reverse()
    path_segs.reverse()
    text = path_nodes[0]
    for sid, nid in zip(path_segs, path_nodes[1:]):
        text += f" --管段 {sid}--> {nid}"
    return text


def _infeasible(code: str, message: str, extra: dict | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "feasible": False,
        "reason_code": code,
        "message": message,
        "closed_valves": [],
        "total_cost": None,
        "node_status": [],
        "segment_status": [],
    }
    if extra:
        out["reason_detail"] = extra
    return out


def _mark_failsafe(
    result: dict[str, Any], regular_count: int = 0
) -> dict[str, Any]:
    """给(无解)结果打上备援求解标记, 保持独立 API 响应结构自描述。"""
    result["failsafe"] = False
    result["plan_type"] = "failsafe"
    result["regular_feasible_subset_count"] = regular_count
    return result
