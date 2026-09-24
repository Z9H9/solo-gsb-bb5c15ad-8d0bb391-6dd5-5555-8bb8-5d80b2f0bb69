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


def solve(payload: dict[str, Any]) -> dict[str, Any]:
    """求解最低代价关阀组合, 返回规划结果(见 README 的返回示例)。"""
    nodes, segments, valves = validate_network(payload)

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

    # 邻接表: adj[u] = [(v, edge_index)]; edge_closed_bit[k] = 该边阀门的位
    adj: list[list[tuple[int, int]]] = [[] for _ in range(n)]
    edge_closed_bit: list[int] = []
    edge_u: list[int] = []
    edge_v: list[int] = []
    for sg in segments:
        u, v = idx[sg["endpoint_a"]], idx[sg["endpoint_b"]]
        e = len(edge_u)
        edge_u.append(u)
        edge_v.append(v)
        bit = (1 << v_idx[sg["valve_id"]]) if sg["valve_id"] is not None else 0
        edge_closed_bit.append(bit)
        adj[u].append((v, e))
        adj[v].append((u, e))

    def reachable(mask: int, sources: set[int]) -> int:
        """从 sources 出发、避开 mask 关阀管段后可达节点的位掩码。"""
        seen = 0
        dq: deque[int] = deque()
        for s in sources:
            if not (seen >> s) & 1:
                seen |= 1 << s
                dq.append(s)
        while dq:
            u = dq.popleft()
            for w, e in adj[u]:
                bit = edge_closed_bit[e]
                if bit and (mask & bit):
                    continue
                if not (seen >> w) & 1:
                    seen |= 1 << w
                    dq.append(w)
        return seen

    leak_bits = sum(1 << x for x in leaks)
    keep_bits = sum(1 << x for x in keeps)
    total_masks = 1 << nv

    # ---- 前置检查(不关任何阀门时) ----
    if not inlets:
        return _infeasible("NO_INLET", "管网中没有进水点, 任何方案都无法供水。")
    base_reach = reachable(0, inlets)
    already_off = keep_bits & ~base_reach
    if already_off:
        names = [nodes[i]["id"] for i in range(n) if (already_off >> i) & 1]
        return _infeasible(
            "GUARANTEED_ALREADY_DISCONNECTED",
            "即使不关任何阀门, 以下必保点也无法连通任何进水点(关阀只会进一步"
            "减少连通性, 故无解): " + ", ".join(names),
            {"starved_guaranteed": names},
        )

    isolating_count = 0
    if not leaks:
        # 没有漏点: 无需关阀即可满足全部要求
        best_mask = 0
        isolating_count = 1
    else:
        # ---- 枚举所有阀门子集; 子集代价按 lowbit DP 递推 ----
        subset_cost = [0] * total_masks
        for m in range(1, total_masks):
            low = m & -m
            subset_cost[m] = (subset_cost[m ^ low]
                              + costs[low.bit_length() - 1])

        best_mask = -1
        best_cost = None
        best_list: tuple[str, ...] = ()

        for m in range(total_masks):
            fed = reachable(m, inlets)
            if (fed & leak_bits) == 0:
                isolating_count += 1
            else:
                continue
            if (fed & keep_bits) != keep_bits:
                continue
            closed = tuple(valves_sorted[b]["id"]
                           for b in range(nv) if (m >> b) & 1)
            c = subset_cost[m]
            if (best_cost is None or c < best_cost
                    or (c == best_cost and closed < best_list)):
                best_cost, best_mask, best_list = c, m, closed

        if best_mask < 0:
            return _explain_infeasible(
                nodes, valves_sorted, adj, edge_closed_bit,
                edge_u, edge_v, segments,
                reachable, inlets, leaks, keeps, isolating_count,
            )

    # ---- 构造最优方案下的节点/管段状态 ----
    fed = reachable(best_mask, inlets)
    # 每个节点在最优方案下可连通的进水点(逐进水点 BFS)
    inlet_of: list[list[str]] = [[] for _ in range(n)]
    for j in sorted(inlets):
        r = reachable(best_mask, {j})
        for i in range(n):
            if (r >> i) & 1:
                inlet_of[i].append(nodes[j]["id"])

    node_status: list[dict[str, Any]] = []
    for i, nd in enumerate(nodes):
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

    closed_valve_ids = {valves_sorted[b]["id"]
                        for b in range(nv) if (best_mask >> b) & 1}
    segment_status: list[dict[str, Any]] = []
    for sg in segments:
        u, v = idx[sg["endpoint_a"]], idx[sg["endpoint_b"]]
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

    closed = [valves_sorted[b]["id"]
              for b in range(nv) if (best_mask >> b) & 1]
    total_cost = sum(costs[b] for b in range(nv) if (best_mask >> b) & 1)
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


def _explain_infeasible(
    nodes, valves_sorted, adj, edge_closed_bit, edge_u, edge_v, segments,
    reachable, inlets, leaks, keeps, isolating_count,
) -> dict[str, Any]:
    """无解原因分析(已有方案都不满足必保约束)。"""
    n = len(nodes)
    nv = len(valves_sorted)
    leak_bits = sum(1 << x for x in leaks)

    if isolating_count == 0:
        # 全关阀后仍有漏点可达进水点: 存在不含阀门的通路
        all_closed = (1 << nv) - 1
        fed_all_closed = reachable(all_closed, inlets)
        reachable_leaks = [nodes[i]["id"] for i in range(n)
                           if ((leak_bits >> i) & 1
                               and (fed_all_closed >> i) & 1)]
        witness = _witness_path(
            nodes, adj, edge_closed_bit, edge_u, edge_v, segments,
            all_closed, inlets, reachable_leaks,
        )
        detail = "关闭全部阀门后, 漏点仍可经不含阀门的管段到达进水点, 无法切断。"
        if witness:
            detail += f" 无阀通路示例: {witness}。"
        return _infeasible(
            "UNCUTTABLE_LEAK_PATH", detail,
            {"reachable_leaks_when_all_closed": reachable_leaks,
             "witness_path": witness},
        )

    # 枚举所有"能隔离漏点"的方案, 求在每一方案中都断水的必保点交集
    always_starved: set[int] = set(keeps)
    witness_valves: list[str] = []
    for m in range(1 << nv):
        fed = reachable(m, inlets)
        if (fed & leak_bits) != 0:
            continue
        starved = {i for i in keeps if not (fed >> i) & 1}
        always_starved &= starved
        if not witness_valves and starved:
            witness_valves = [valves_sorted[b]["id"]
                              for b in range(nv) if (m >> b) & 1]

    if always_starved:
        names = [nodes[i]["id"] for i in sorted(always_starved)]
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


def _witness_path(nodes, adj, edge_closed_bit, edge_u, edge_v, segments,
                  mask, inlets, target_node_ids) -> str:
    """在关闭 mask 后仍走得通的图里, BFS 重建一条 进水点->目标漏点 的路径。"""
    if not target_node_ids:
        return ""
    n = len(nodes)
    id2i = {nd["id"]: i for i, nd in enumerate(nodes)}
    targets = {id2i[t] for t in target_node_ids}
    prev_node = [-1] * n
    prev_edge = [-1] * n
    dq: deque[int] = deque()
    for s in inlets:
        if prev_node[s] == -1:
            prev_node[s] = s
            dq.append(s)
    found = -1
    while dq:
        u = dq.popleft()
        if u in targets:
            found = u
            break
        for w, e in adj[u]:
            bit = edge_closed_bit[e]
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
        path_nodes.append(nodes[cur]["id"])
        path_segs.append(segments[prev_edge[cur]]["id"])
        cur = prev_node[cur]
    path_nodes.append(nodes[cur]["id"])
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
