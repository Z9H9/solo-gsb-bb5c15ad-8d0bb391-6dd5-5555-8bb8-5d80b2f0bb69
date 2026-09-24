"""管网漏点隔离求解器。

模型:
  - 节点 = 无向管网上的点; 每个节点有角色: inlet(进水点)/leak(漏点)/
    must_keep(必保供水点)/normal(普通点)。
  - 管段 = 无向边; 每条管段最多绑定 1 个阀门。关闭阀门即删除对应管段。
  - 目标: 选择代价之和最小的阀门组合, 使
      (A) 每个漏点与所有进水点都不连通(漏点被隔离);
      (B) 每个必保点仍与至少一个进水点连通。
    同代价时, 取关闭阀门编号升序表的字典序最小者。

两种求解:
  - solve(payload): 常规规划, 只要求方案本身满足 (A)(B)。
  - solve_failover(payload): 单阀失效备援规划。计划关闭的每一枚阀门都可能
    "关不上" 而保持开启; 要求方案正常执行时, 以及任意一枚计划阀门失效(其
    绑定管段保持通水)时, (A)(B) 仍然成立。位掩码 M 的方案备援可行, 当且
    仅当 M 本身以及每个 M\\{j}(j∈M) 都是常规可行方案。

阀门最多 14 个, 枚举全部 2^V 个子集(V<=14 => 16384), 对每个子集做
BFS 连通性检查即可, 简单且确定。
"""

from __future__ import annotations

from collections import deque
from typing import Any

MAX_VALVES = 14


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


class _Graph:
    """校验后管网的图表示: 邻接表 + 关阀位掩码 + BFS 连通性。"""

    def __init__(self, nodes: list[dict[str, Any]],
                 segments: list[dict[str, Any]],
                 valves: list[dict[str, Any]]):
        self.nodes = nodes
        self.segments = segments
        self.ids = [nd["id"] for nd in nodes]
        self.idx = {nid: i for i, nid in enumerate(self.ids)}
        n = len(self.ids)
        self.n = n

        self.inlet_set = {i for i, nd in enumerate(nodes)
                          if nd["role"] == "inlet"}
        self.leak_set = {i for i, nd in enumerate(nodes)
                         if nd["role"] == "leak"}
        self.keep_set = {i for i, nd in enumerate(nodes)
                         if nd["role"] == "must_keep"}
        self.leak_bits = sum(1 << i for i in self.leak_set)
        self.keep_bits = sum(1 << i for i in self.keep_set)

        # 阀门按编号升序: 保证位序确定, 同代价时用编号列表直接做字典序比较
        self.valves_sorted = sorted(valves, key=lambda vl: vl["id"])
        self.nv = len(self.valves_sorted)
        self.total_masks = 1 << self.nv
        v_idx = {vl["id"]: i for i, vl in enumerate(self.valves_sorted)}
        self.costs = [vl["cost"] for vl in self.valves_sorted]

        # 邻接表: adj[u] = [(v, edge_index)]; edge_closed_bit[e] = 该边阀门的位
        adj: list[list[tuple[int, int]]] = [[] for _ in range(n)]
        self.edge_closed_bit: list[int] = []
        for sg in segments:
            u, v = self.idx[sg["endpoint_a"]], self.idx[sg["endpoint_b"]]
            e = len(self.edge_closed_bit)
            bit = ((1 << v_idx[sg["valve_id"]])
                   if sg["valve_id"] is not None else 0)
            self.edge_closed_bit.append(bit)
            adj[u].append((v, e))
            adj[v].append((u, e))
        self.adj = adj

    def reachable(self, mask: int, sources: set[int]) -> int:
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

    def subset_costs(self) -> list[int]:
        """所有阀门子集的代价; 按 lowbit DP 递推。"""
        costs = [0] * self.total_masks
        for m in range(1, self.total_masks):
            low = m & -m
            costs[m] = costs[m ^ low] + self.costs[low.bit_length() - 1]
        return costs

    def closed_names(self, mask: int) -> list[str]:
        """mask 中关闭的阀门编号(编号升序)。"""
        return [self.valves_sorted[b]["id"]
                for b in range(self.nv) if (mask >> b) & 1]


def solve(payload: dict[str, Any]) -> dict[str, Any]:
    """求解最低代价关阀组合, 返回规划结果(见 README 的返回示例)。"""
    nodes, segments, valves = validate_network(payload)
    g = _Graph(nodes, segments, valves)

    # ---- 前置检查(不关任何阀门时) ----
    if not g.inlet_set:
        return _infeasible("NO_INLET",
                           "管网中没有进水点, 任何方案都无法供水。")
    base_reach = g.reachable(0, g.inlet_set)
    already_off = g.keep_bits & ~base_reach
    if already_off:
        names = [g.nodes[i]["id"] for i in range(g.n)
                 if (already_off >> i) & 1]
        return _infeasible(
            "GUARANTEED_ALREADY_DISCONNECTED",
            "即使不关任何阀门, 以下必保点也无法连通任何进水点(关阀只会进一步"
            "减少连通性, 故无解): " + ", ".join(names),
            {"starved_guaranteed": names},
        )

    isolating_count = 0
    if not g.leak_set:
        # 没有漏点: 无需关阀即可满足全部要求
        best_mask = 0
        isolating_count = 1
    else:
        subset_cost = g.subset_costs()
        best_mask = -1
        best_cost = None
        best_list: tuple[str, ...] = ()

        for m in range(g.total_masks):
            fed = g.reachable(m, g.inlet_set)
            if (fed & g.leak_bits) == 0:
                isolating_count += 1
            else:
                continue
            if (fed & g.keep_bits) != g.keep_bits:
                continue
            closed = tuple(g.closed_names(m))
            c = subset_cost[m]
            if (best_cost is None or c < best_cost
                    or (c == best_cost and closed < best_list)):
                best_cost, best_mask, best_list = c, m, closed

        if best_mask < 0:
            return _explain_infeasible(g, isolating_count)

    result = _build_statuses(g, best_mask, isolating_count)
    result["solver"] = "base"
    return result


def solve_failover(payload: dict[str, Any]) -> dict[str, Any]:
    """求解单阀失效备援关阀组合。

    方案 mask 备援可行 <=> mask 常规可行, 且对其中每个已关阀 j,
    mask\\{j}(j 关不上、保持开启)也常规可行。无解时用 infeasible_kind
    区分: no_base_plan(本来就没有常规可行方案) / no_failover_plan
    (常规方案存在, 但没有备援方案)。
    """
    nodes, segments, valves = validate_network(payload)
    g = _Graph(nodes, segments, valves)

    # ---- 与常规规划一致的前置检查; 此类情况下"原本就没有常规可行方案" ----
    if not g.inlet_set:
        return _infeasible(
            "NO_INLET", "管网中没有进水点, 任何方案都无法供水。",
            solver="failover", kind="no_base_plan")
    base_reach = g.reachable(0, g.inlet_set)
    already_off = g.keep_bits & ~base_reach
    if already_off:
        names = [g.nodes[i]["id"] for i in range(g.n)
                 if (already_off >> i) & 1]
        return _infeasible(
            "GUARANTEED_ALREADY_DISCONNECTED",
            "即使不关任何阀门, 以下必保点也无法连通任何进水点(关阀只会进一步"
            "减少连通性, 故无解): " + ", ".join(names),
            {"starved_guaranteed": names},
            solver="failover", kind="no_base_plan")

    # ---- 预计算每个阀门子集的常规可行性 ----
    # fed[m]: 关闭 m 时所有进水点可达节点; feasible[m]: 是否满足 (A)(B)
    isolating_count = 0
    feasible = [False] * g.total_masks
    for m in range(g.total_masks):
        fed = g.reachable(m, g.inlet_set)
        isolated = (fed & g.leak_bits) == 0
        if isolated:
            isolating_count += 1
        feasible[m] = isolated and (fed & g.keep_bits) == g.keep_bits

    base_feasible = [m for m in range(g.total_masks) if feasible[m]]
    if not base_feasible:
        # 常规规划本身无解: 复用常规无解原因分析
        result = _explain_infeasible(g, isolating_count)
        result["solver"] = "failover"
        result["infeasible_kind"] = "no_base_plan"
        return result

    # ---- 在常规可行方案中枚举备援可行方案 ----
    subset_cost = g.subset_costs()
    best_mask = -1
    best_cost = None
    best_list: tuple[str, ...] = ()
    failover_count = 0
    for m in base_feasible:
        # 每一枚计划阀门关不上时, 少关这枚阀的方案仍须常规可行
        robust = True
        bits = m
        while bits:
            low = bits & -bits
            if not feasible[m ^ low]:
                robust = False
                break
            bits ^= low
        if not robust:
            continue
        failover_count += 1
        closed = tuple(g.closed_names(m))
        c = subset_cost[m]
        if (best_cost is None or c < best_cost
                or (c == best_cost and closed < best_list)):
            best_cost, best_mask, best_list = c, m, closed

    if best_mask < 0:
        return _explain_no_failover(g, base_feasible, subset_cost)

    # ---- 构造正常执行 + 逐阀失效校验结果 ----
    result = _build_statuses(g, best_mask, isolating_count)
    result["solver"] = "failover"
    result["base_feasible_subset_count"] = len(base_feasible)
    result["failover_candidate_count"] = failover_count
    result["normal_check"] = _scenario_check(g, best_mask, None)
    result["failure_checks"] = [
        _scenario_check(g, best_mask ^ (1 << b), b)
        for b in range(g.nv) if (best_mask >> b) & 1
    ]
    n_closed = len(result["closed_valves"])
    result["message"] = (
        f"已找到单阀失效备援方案: 关闭 {n_closed} 个阀门, 总代价 "
        f"{result['total_cost']}。正常执行及任意一枚计划阀门关不上"
        f"(保持开启)时, 全部漏点均与进水点断开、全部必保点仍有供水。"
    )
    return result


def _scenario_check(g: _Graph, mask: int, failed_bit: int | None) -> dict[str, Any]:
    """一种执行情形下的 (A)(B) 校验; failed_bit=None 表示正常执行。"""
    fed = g.reachable(mask, g.inlet_set)
    leaks_reachable = [g.nodes[i]["id"] for i in range(g.n)
                       if ((g.leak_bits >> i) & 1 and (fed >> i) & 1)]
    keeps_starved = [g.nodes[i]["id"] for i in range(g.n)
                     if ((g.keep_bits >> i) & 1 and not (fed >> i) & 1)]
    check: dict[str, Any] = {
        "scenario": "normal" if failed_bit is None else "valve_failure",
        "failed_valve": (None if failed_bit is None
                         else g.valves_sorted[failed_bit]["id"]),
        "effective_closed_valves": g.closed_names(mask),
        "leaks_isolated": not leaks_reachable,
        "leaks_reachable": leaks_reachable,
        "keeps_supplied": not keeps_starved,
        "keeps_starved": keeps_starved,
        "ok": not leaks_reachable and not keeps_starved,
    }
    if failed_bit is not None:
        vl = g.valves_sorted[failed_bit]
        check["segment_id"] = vl["segment_id"]
        check["cost"] = vl["cost"]
    return check


def _explain_no_failover(g: _Graph, base_feasible: list[int],
                         subset_cost: list[int]) -> dict[str, Any]:
    """常规可行方案存在, 但没有任何备援可行方案。"""
    # 取最低代价(同代价字典序最小)的常规方案, 逐阀展示其失效后果
    base_best = -1
    base_best_cost = None
    base_best_list: tuple[str, ...] = ()
    for m in base_feasible:
        closed = tuple(g.closed_names(m))
        c = subset_cost[m]
        if (base_best_cost is None or c < base_best_cost
                or (c == base_best_cost and closed < base_best_list)):
            base_best_cost, base_best, base_best_list = c, m, closed

    checks = [
        _scenario_check(g, base_best ^ (1 << b), b)
        for b in range(g.nv) if (base_best >> b) & 1
    ]
    failing = [chk for chk in checks if not chk["ok"]]
    fail_desc = "; ".join(_describe_failure(chk) for chk in failing)
    message = (
        f"存在 {len(base_feasible)} 个常规可行方案, 但没有任何方案能承受单阀"
        f"失效。最低代价常规方案为关闭 "
        f"{', '.join(base_best_list) or '(无)'}(代价 {base_best_cost}); "
    )
    if not checks:
        message += "该方案不关任何阀门, 无需备援。"
    else:
        message += f"当其计划阀门关不上时: {fail_desc}。"

    return _infeasible(
        "NO_FAILOVER_PLAN", message,
        {
            "base_feasible_subset_count": len(base_feasible),
            "best_base_plan": {
                "closed_valves": list(base_best_list),
                "total_cost": base_best_cost,
                "failure_checks": checks,
            },
        },
        solver="failover", kind="no_failover_plan",
    )


def _describe_failure(chk: dict[str, Any]) -> str:
    reasons = []
    if chk["leaks_reachable"]:
        reasons.append("漏点 " + ", ".join(chk["leaks_reachable"]) + " 仍通进水点")
    if chk["keeps_starved"]:
        reasons.append("必保点 " + ", ".join(chk["keeps_starved"]) + " 断水")
    return f"阀门 {chk['failed_valve']} 关不上 -> {'; '.join(reasons)}"


def _build_statuses(g: _Graph, best_mask: int,
                    isolating_count: int) -> dict[str, Any]:
    """构造某方案下的节点/管段状态及汇总字段(方案正常执行视角)。"""
    n = g.n
    fed = g.reachable(best_mask, g.inlet_set)
    # 每个节点在该方案下可连通的进水点(逐进水点 BFS)
    inlet_of: list[list[str]] = [[] for _ in range(n)]
    for j in sorted(g.inlet_set):
        r = g.reachable(best_mask, {j})
        for i in range(n):
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

    closed_valve_ids = set(g.closed_names(best_mask))
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

    closed = g.closed_names(best_mask)
    total_cost = sum(g.costs[b] for b in range(g.nv)
                     if (best_mask >> b) & 1)
    return {
        "feasible": True,
        "total_cost": total_cost,
        "closed_valves": closed,
        "closed_count": len(closed),
        "subset_count": g.total_masks,
        "isolating_subset_count": isolating_count,
        "node_status": node_status,
        "segment_status": segment_status,
        "message": (f"已找到最优关阀方案: 关闭 {len(closed)} 个阀门, "
                    f"总代价 {total_cost}。"),
    }


def _explain_infeasible(g: _Graph, isolating_count: int) -> dict[str, Any]:
    """常规无解原因分析(已有方案都不满足必保约束)。"""
    n, nv = g.n, g.nv

    if isolating_count == 0:
        # 全关阀后仍有漏点可达进水点: 存在不含阀门的通路
        all_closed = (1 << nv) - 1
        fed_all_closed = g.reachable(all_closed, g.inlet_set)
        reachable_leaks = [g.nodes[i]["id"] for i in range(n)
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
    always_starved: set[int] = set(g.keep_set)
    witness_valves: list[str] = []
    for m in range(1 << nv):
        fed = g.reachable(m, g.inlet_set)
        if (fed & g.leak_bits) != 0:
            continue
        starved = {i for i in g.keep_set if not (fed >> i) & 1}
        always_starved &= starved
        if not witness_valves and starved:
            witness_valves = g.closed_names(m)

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
    for s in g.inlet_set:
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


def _infeasible(code: str, message: str, extra: dict | None = None,
                *, solver: str = "base", kind: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "feasible": False,
        "solver": solver,
        "reason_code": code,
        "message": message,
        "closed_valves": [],
        "total_cost": None,
        "node_status": [],
        "segment_status": [],
    }
    if kind is not None:
        out["infeasible_kind"] = kind
    if extra:
        out["reason_detail"] = extra
    return out
