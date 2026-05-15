"""
修复算子 v2
===========
将被破坏算子移除的订单重新插入解中。

v2 改进:
1. try_insert_as_new_trip 支持游轮订单（供应地/需求地为游轮）
2. compute_insertion_profit 使用实际位置（修复destroy后元数据不一致问题）
3. 新增 BatchNewTripInsertion 批量新建行程算子
4. 修复利润比较逻辑（使用相对增量而非固定>0阈值）
"""

import random
import math
from typing import Dict, List, Optional, Set, Tuple
from abc import ABC, abstractmethod

from ..map_data import MapData, GeoCoord, get_map_data
from ..cruise_ship import (
    CruiseRoute,
    RendezvousPredictor,
    get_cruise_route,
    get_rendezvous_predictor,
)
from ..drone import (
    DroneFlightCalculator,
    DRONE_SPEED_MPS,
    DRONE_MAX_PAYLOAD_KG,
    DRONE_BATTERY_DRAIN_RATE,
    DRONE_FLIGHT_ALTITUDE,
    DRONE_SWAP_TIME_SECONDS,
    DRONE_VERTICAL_SPEED_MPS,
    BATTERY_SWAP_COST,
    SIMULATION_DURATION_SECONDS,
)

BATTERY_RETURN_THRESHOLD = 20.0
BATTERY_SWAP_THRESHOLD = 30.0
from ..order import Order, OrderManager
from .solution import Solution, DronePlan, FlightLeg
from .evaluator import SolutionEvaluator


# ============================================================
# 修复算子基类
# ============================================================


class RepairOperator(ABC):
    """修复算子基类"""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def repair(
        self,
        solution: Solution,
        removed_orders: Set[int],
        order_manager: OrderManager,
        evaluator: SolutionEvaluator,
        **kwargs,
    ) -> Solution:
        pass


# ============================================================
# 辅助函数
# ============================================================


def _make_flight_leg(
    from_loc: str,
    to_loc: str,
    depart_time: float,
    battery: float,
    payload: float,
    map_data: MapData,
    calculator: DroneFlightCalculator,
    predictor: RendezvousPredictor,
    cruise: CruiseRoute,
) -> Tuple[Optional[FlightLeg], float, float]:
    """
    计算从 from_loc 飞到 to_loc 的飞行leg。
    自动处理游轮航线（静态↔游轮）。

    返回: (leg, arrive_time, battery_after) 或 (None, 0, 0) 如果不可行
    """
    if from_loc == to_loc:
        return None, depart_time, battery

    try:
        is_cruise_from = from_loc == "游轮"
        is_cruise_to = to_loc == "游轮"

        if is_cruise_from and not is_cruise_to:
            cruise_pos = cruise.position_at_time(depart_time)
            info = calculator.fly_from_cruise(
                to_loc, depart_time, cruise_pos, battery, payload
            )
            if not info["feasible"]:
                return None, 0, 0
            arrive = info["arrival_time_seconds"]
            leg = FlightLeg(
                from_location=from_loc,
                to_location=to_loc,
                depart_time=depart_time,
                arrive_time=arrive,
                flight_distance_km=info["total_distance_km"],
                battery_before=battery,
                battery_after=info["battery_remaining"],
            )
            return leg, arrive, info["battery_remaining"]

        elif is_cruise_to:
            rendezvous, arrival, flight_dist = predictor.compute_rendezvous_from_node(
                from_loc, depart_time, map_data
            )
            landing_alt = max(0, DRONE_FLIGHT_ALTITUDE - cruise.WAYPOINTS[0].alt)
            landing_dist = landing_alt / 1000.0
            landing_time = landing_alt / DRONE_VERTICAL_SPEED_MPS
            total_dist = flight_dist + landing_dist
            total_time = (arrival - depart_time) + landing_time
            battery_consumed = calculator.battery_consumption(total_dist)
            battery_after = battery - battery_consumed

            if battery_after < 0:
                return None, 0, 0
            arrive_time = depart_time + total_time
            if arrive_time > SIMULATION_DURATION_SECONDS:
                return None, 0, 0

            leg = FlightLeg(
                from_location=from_loc,
                to_location=to_loc,
                cruise_target=rendezvous,
                depart_time=depart_time,
                arrive_time=arrive_time,
                flight_distance_km=total_dist,
                battery_before=battery,
                battery_after=battery_after,
            )
            return leg, arrive_time, battery_after

        else:
            info = calculator.fly_between_nodes(from_loc, to_loc, battery, payload)
            if not info["feasible"]:
                return None, 0, 0
            arrive_time = depart_time + info["total_time_seconds"]
            if arrive_time > SIMULATION_DURATION_SECONDS:
                return None, 0, 0

            leg = FlightLeg(
                from_location=from_loc,
                to_location=to_loc,
                depart_time=depart_time,
                arrive_time=arrive_time,
                flight_distance_km=info["total_distance_km"],
                battery_before=battery,
                battery_after=info["battery_remaining"],
            )
            return leg, arrive_time, info["battery_remaining"]
    except Exception:
        return None, 0, 0


def _get_plan_end_state(plan: DronePlan) -> Tuple[str, float, float]:
    """获取无人机计划的终态 (位置, 时间, 电量)"""
    if not plan.legs:
        return plan.initial_location, 0.0, 100.0
    last = plan.legs[-1]
    end_time = last.arrive_time
    if last.swap_battery:
        end_time += DRONE_SWAP_TIME_SECONDS
    end_battery = 100.0 if last.swap_battery else last.battery_after
    return last.to_location, end_time, end_battery


def _simulate_positions(plan: DronePlan) -> List[str]:
    """
    模拟无人机位置序列。
    返回每个leg开始前无人机的实际位置（修复destroy后元数据不一致问题）。
    """
    positions = []
    loc = plan.initial_location
    for leg in plan.legs:
        positions.append(loc)
        if leg.from_location != leg.to_location or leg.flight_distance_km > 0:
            loc = leg.to_location
    return positions


# ============================================================
# compute_insertion_profit - 改进版
# ============================================================


def compute_insertion_profit(
    solution: Solution,
    order: Order,
    drone_id: str,
    leg_index: int,
    order_manager: OrderManager,
    evaluator: SolutionEvaluator,
) -> Tuple[float, Optional[Solution]]:
    """
    估算将订单插入到指定位置后的利润。

    改进: 使用模拟位置而非leg.from_location（修复destroy后元数据不一致问题）。

    返回: (利润, 试探解) 或 (负无穷, None)
    """
    trial = solution.deep_copy()
    plan = trial.drone_plans[drone_id]

    if leg_index >= len(plan.legs):
        return float("-inf"), None

    leg = plan.legs[leg_index]

    # 获取该leg开始前的实际位置
    positions = _simulate_positions(plan)
    actual_pos = positions[leg_index]

    # 检查载重
    current_payload = sum(
        order_manager.orders[oid].weight_kg
        for oid in leg.load_orders
        if oid in order_manager.orders
    )
    if current_payload + order.weight_kg > DRONE_MAX_PAYLOAD_KG:
        return float("-inf"), None

    # 检查供应地（使用实际位置）
    if actual_pos != order.supply_location:
        return float("-inf"), None

    # 检查需求地：是否有后续leg到达需求地
    demand_leg_found = False
    for j in range(leg_index, len(plan.legs)):
        if plan.legs[j].to_location == order.demand_location:
            demand_leg_found = True
            plan.legs[j].unload_orders.append(order.order_id)
            break

    if not demand_leg_found:
        return float("-inf"), None

    # 添加装货
    leg.load_orders.append(order.order_id)
    trial.unassigned_order_ids.discard(order.order_id)

    # 重新评估
    result = evaluator.evaluate(trial)
    if not result.feasible:
        return float("-inf"), None

    return result.net_profit, trial


# ============================================================
# try_insert_as_new_trip - 重写版（支持游轮）
# ============================================================


def try_insert_as_new_trip(
    solution: Solution,
    order: Order,
    order_manager: OrderManager,
    evaluator: SolutionEvaluator,
    map_data: MapData,
    calculator: DroneFlightCalculator,
    predictor: RendezvousPredictor,
) -> Optional[Solution]:
    """
    尝试为订单创建一趟新的飞行任务。

    支持所有供应/需求组合：
    - 静态 → 静态
    - 静态 → 游轮
    - 游轮 → 静态
    - 游轮 → 游轮（防御性处理）
    """
    cruise = get_cruise_route()
    trial = solution.deep_copy()
    best_trial = None
    best_profit = float("-inf")

    for drone_id, plan in trial.drone_plans.items():
        end_loc, end_time, end_battery = _get_plan_end_state(plan)

        if end_time >= SIMULATION_DURATION_SECONDS - 300:
            continue

        supply = order.supply_location
        demand = order.demand_location
        legs_to_add = []
        current_loc = end_loc
        current_time = end_time
        current_battery = end_battery

        # === Phase 1: 飞到供应地 ===
        if current_loc != supply:
            leg, t, bat = _make_flight_leg(
                current_loc,
                supply,
                current_time,
                current_battery,
                0.0,
                map_data,
                calculator,
                predictor,
                cruise,
            )
            if leg is None or t > SIMULATION_DURATION_SECONDS:
                continue
            legs_to_add.append(leg)
            current_time = t
            current_battery = bat
            current_loc = supply

        # 等待订单生成
        if order.generation_time_seconds > current_time:
            current_time = order.generation_time_seconds

        # === Phase 2: 装载订单（原地leg） ===
        load_leg = FlightLeg(
            from_location=current_loc,
            to_location=current_loc,
            load_orders=[order.order_id],
            depart_time=current_time,
            arrive_time=current_time,
            flight_distance_km=0.0,
            battery_before=current_battery,
            battery_after=current_battery,
        )
        legs_to_add.append(load_leg)

        # === Phase 3: 飞到需求地 ===
        delivery_leg, t, bat = _make_flight_leg(
            current_loc,
            demand,
            current_time,
            current_battery,
            order.weight_kg,
            map_data,
            calculator,
            predictor,
            cruise,
        )
        if delivery_leg is None or t > SIMULATION_DURATION_SECONDS:
            continue
        delivery_leg.unload_orders = [order.order_id]
        legs_to_add.append(delivery_leg)
        current_time = t
        current_battery = bat
        current_loc = demand

        # === Phase 4: 电量管理 ===
        land_airports = [ap.name for ap in map_data.get_land_airports()]
        if current_battery < BATTERY_RETURN_THRESHOLD and current_time < SIMULATION_DURATION_SECONDS - 300:
            if current_loc in land_airports:
                delivery_leg.swap_battery = True
            elif current_loc != "游轮":
                try:
                    nearest, _ = map_data.find_nearest_land_airport(current_loc)
                    swap_leg, t_swap, bat_swap = _make_flight_leg(
                        current_loc,
                        nearest,
                        current_time,
                        current_battery,
                        0.0,
                        map_data,
                        calculator,
                        predictor,
                        cruise,
                    )
                    if (
                        swap_leg
                        and t_swap + DRONE_SWAP_TIME_SECONDS
                        < SIMULATION_DURATION_SECONDS
                    ):
                        swap_leg.swap_battery = True
                        legs_to_add.append(swap_leg)
                except Exception:
                    pass

        # === Phase 5: 评估 ===
        trial2 = trial.deep_copy()
        for l in legs_to_add:
            trial2.drone_plans[drone_id].legs.append(l)
        trial2.unassigned_order_ids.discard(order.order_id)

        result = evaluator.evaluate(trial2)
        if result.feasible and result.net_profit > best_profit:
            best_trial = trial2
            best_profit = result.net_profit

    return best_trial


# ============================================================
# Greedy Insertion - 贪心插入（改进利润比较）
# ============================================================


class GreedyInsertion(RepairOperator):
    """
    Greedy Insertion (v2 with exploration + unassigned orders)
    ==========================================================
    按订单价值降序，每个插入到利润增量最大的位置。

    改进：
    1. 概率跳过piggyback，强制new trip（搜索多样性）
    2. 注入最优未分配订单，替换低价值被移除订单
    3. 利润比较使用当前解为基准
    """

    def __init__(self, piggyback_prob: float = 0.6):
        super().__init__("greedy_insertion")
        self.piggyback_prob = piggyback_prob

    def repair(
        self,
        solution: Solution,
        removed_orders: Set[int],
        order_manager: OrderManager,
        evaluator: SolutionEvaluator,
        **kwargs,
    ) -> Solution:
        map_data = kwargs.get("map_data", get_map_data())
        calculator = DroneFlightCalculator(map_data)
        predictor = get_rendezvous_predictor()

        # 收集可插入订单 = 被移除订单 + 高分未分配订单
        orders_to_insert = []
        seen_ids = set()

        for oid in removed_orders:
            if oid in order_manager.orders:
                order = order_manager.orders[oid]
                if order.is_deliverable_at(0):
                    orders_to_insert.append(order)
                    seen_ids.add(oid)

        # 从解中获取未分配订单池
        unassigned_ids = list(solution.unassigned_order_ids - seen_ids)
        scored_unassigned = []
        for oid in unassigned_ids:
            if oid in order_manager.orders:
                order = order_manager.orders[oid]
                if order.is_deliverable_at(0):
                    score = evaluator.compute_order_score(order)
                    if score > 0:
                        scored_unassigned.append((score, order))

        scored_unassigned.sort(key=lambda x: -x[0])
        # 取前20个最佳的未分配订单加入插入池
        for score, order in scored_unassigned[:20]:
            if order.order_id not in seen_ids:
                orders_to_insert.append(order)
                seen_ids.add(order.order_id)

        orders_to_insert.sort(key=lambda o: -o.full_income)

        current_sol = solution.deep_copy()
        base_result = evaluator.evaluate(current_sol)
        current_profit = (
            base_result.net_profit if base_result.feasible else float("-inf")
        )

        for order in orders_to_insert:
            best_profit = current_profit
            best_trial = None

            # 概率跳过piggyback（增加多样性）
            use_piggyback = random.random() < self.piggyback_prob

            if use_piggyback:
                for drone_id, plan in current_sol.drone_plans.items():
                    for i in range(len(plan.legs)):
                        profit, trial = compute_insertion_profit(
                            current_sol, order, drone_id, i, order_manager, evaluator
                        )
                        if profit > best_profit and trial is not None:
                            best_profit = profit
                            best_trial = trial

            # 总是尝试new trip
            new_trip_trial = try_insert_as_new_trip(
                current_sol,
                order,
                order_manager,
                evaluator,
                map_data,
                calculator,
                predictor,
            )
            if new_trip_trial is not None:
                result = evaluator.evaluate(new_trip_trial)
                if result.feasible and result.net_profit > best_profit:
                    best_trial = new_trip_trial
                    best_profit = result.net_profit

            if best_trial is not None:
                current_sol = best_trial
                current_profit = best_profit

        return current_sol


# ============================================================
# Regret-2 Insertion（改进利润比较）
# ============================================================


class Regret2Insertion(RepairOperator):
    """
    Regret-2 Insertion
    ==================
    优先插入"如果不现在插、以后就没好位置了"的订单。
    regret = 最好增量 - 第二好增量
    """

    def __init__(self):
        super().__init__("regret_2_insertion")

    def repair(
        self,
        solution: Solution,
        removed_orders: Set[int],
        order_manager: OrderManager,
        evaluator: SolutionEvaluator,
        **kwargs,
    ) -> Solution:
        map_data = kwargs.get("map_data", get_map_data())
        calculator = DroneFlightCalculator(map_data)
        predictor = get_rendezvous_predictor()

        orders_to_insert = []
        for oid in removed_orders:
            if oid in order_manager.orders:
                order = order_manager.orders[oid]
                if order.is_deliverable_at(0):
                    orders_to_insert.append(order)

        current_sol = solution.deep_copy()
        base_result = evaluator.evaluate(current_sol)
        current_profit = (
            base_result.net_profit if base_result.feasible else float("-inf")
        )
        remaining = list(orders_to_insert)

        while remaining:
            best_insertions = {}

            for order in remaining:
                insertions = []

                for drone_id, plan in current_sol.drone_plans.items():
                    for i in range(len(plan.legs)):
                        profit, trial = compute_insertion_profit(
                            current_sol, order, drone_id, i, order_manager, evaluator
                        )
                        if trial is not None:
                            insertions.append((profit, trial))

                new_trip = try_insert_as_new_trip(
                    current_sol,
                    order,
                    order_manager,
                    evaluator,
                    map_data,
                    calculator,
                    predictor,
                )
                if new_trip is not None:
                    result = evaluator.evaluate(new_trip)
                    if result.feasible:
                        insertions.append((result.net_profit, new_trip))

                insertions.sort(key=lambda x: -x[0])
                best_insertions[order.order_id] = insertions

            best_order = None
            best_regret = -1
            best_trial_for_order = None
            best_profit_for_order = current_profit

            for order in remaining:
                insertions = best_insertions.get(order.order_id, [])
                if len(insertions) >= 2:
                    regret = insertions[0][0] - insertions[1][0]
                elif len(insertions) == 1:
                    regret = insertions[0][0]  # 只有1个选择 → 很迫切
                else:
                    regret = float("-inf")

                if regret > best_regret:
                    best_regret = regret
                    best_order = order
                    if insertions:
                        best_trial_for_order = insertions[0][1]
                        best_profit_for_order = insertions[0][0]

            if best_order is None or best_trial_for_order is None:
                break

            if best_profit_for_order <= current_profit:
                break

            current_sol = best_trial_for_order
            current_profit = best_profit_for_order
            remaining.remove(best_order)

        return current_sol


# ============================================================
# Regret-3 Insertion（改进利润比较）
# ============================================================


class Regret3Insertion(RepairOperator):
    """
    Regret-3 Insertion
    ==================
    同Regret-2但考虑第三好的位置。
    """

    def __init__(self):
        super().__init__("regret_3_insertion")

    def repair(
        self,
        solution: Solution,
        removed_orders: Set[int],
        order_manager: OrderManager,
        evaluator: SolutionEvaluator,
        **kwargs,
    ) -> Solution:
        map_data = kwargs.get("map_data", get_map_data())
        calculator = DroneFlightCalculator(map_data)
        predictor = get_rendezvous_predictor()

        orders_to_insert = []
        for oid in removed_orders:
            if oid in order_manager.orders:
                order = order_manager.orders[oid]
                if order.is_deliverable_at(0):
                    orders_to_insert.append(order)

        current_sol = solution.deep_copy()
        base_result = evaluator.evaluate(current_sol)
        current_profit = (
            base_result.net_profit if base_result.feasible else float("-inf")
        )
        remaining = list(orders_to_insert)

        while remaining:
            best_order = None
            best_regret = -1
            best_trial = None
            best_trial_profit = current_profit

            for order in remaining:
                insertions = []

                for drone_id, plan in current_sol.drone_plans.items():
                    for i in range(len(plan.legs)):
                        profit, trial = compute_insertion_profit(
                            current_sol, order, drone_id, i, order_manager, evaluator
                        )
                        if trial is not None:
                            insertions.append((profit, trial))

                new_trip = try_insert_as_new_trip(
                    current_sol,
                    order,
                    order_manager,
                    evaluator,
                    map_data,
                    calculator,
                    predictor,
                )
                if new_trip is not None:
                    result = evaluator.evaluate(new_trip)
                    if result.feasible:
                        insertions.append((result.net_profit, new_trip))

                insertions.sort(key=lambda x: -x[0])

                if len(insertions) >= 3:
                    regret = insertions[0][0] - insertions[2][0]
                elif len(insertions) >= 2:
                    regret = insertions[0][0] - insertions[1][0] + 10
                elif len(insertions) == 1:
                    regret = insertions[0][0] + 20
                else:
                    regret = float("-inf")

                if regret > best_regret:
                    best_regret = regret
                    best_order = order
                    if insertions:
                        best_trial = insertions[0][1]
                        best_trial_profit = insertions[0][0]

            if best_order is None or best_trial is None:
                break

            if best_trial_profit <= current_profit:
                break

            current_sol = best_trial
            current_profit = best_trial_profit
            remaining.remove(best_order)

        return current_sol


# ============================================================
# Batch New Trip Insertion - 批量新建行程
# ============================================================


class BatchNewTripInsertion(RepairOperator):
    """
    Batch New Trip Insertion
    ========================
    将多个待插入订单按供应地分组，
    为每架无人机构建批量新行程（一趟送多单）。

    优势：共享飞往供应地的成本，提高利润率。
    """

    def __init__(self):
        super().__init__("batch_new_trip_insertion")

    def repair(
        self,
        solution: Solution,
        removed_orders: Set[int],
        order_manager: OrderManager,
        evaluator: SolutionEvaluator,
        **kwargs,
    ) -> Solution:
        map_data = kwargs.get("map_data", get_map_data())
        calculator = DroneFlightCalculator(map_data)
        predictor = get_rendezvous_predictor()
        cruise = get_cruise_route()

        orders_to_insert = []
        for oid in removed_orders:
            if oid in order_manager.orders:
                order = order_manager.orders[oid]
                if order.is_deliverable_at(0):
                    orders_to_insert.append(order)

        if not orders_to_insert:
            return solution.deep_copy()

        current_sol = solution.deep_copy()
        base_result = evaluator.evaluate(current_sol)
        current_profit = (
            base_result.net_profit if base_result.feasible else float("-inf")
        )
        inserted_ids = set()

        for drone_id, plan in current_sol.drone_plans.items():
            end_loc, end_time, end_battery = _get_plan_end_state(plan)

            if end_time >= SIMULATION_DURATION_SECONDS - 300:
                continue

            remaining = [o for o in orders_to_insert if o.order_id not in inserted_ids]
            if not remaining:
                break

            # 按供应地分组
            supply_groups: Dict[str, List[Order]] = {}
            for o in remaining:
                supply_groups.setdefault(o.supply_location, []).append(o)

            # 选最优供应地组（按可装载的总收入）
            best_group = None
            best_group_score = -1
            for supply, group_orders in supply_groups.items():
                if end_loc != supply:
                    leg, t, bat = _make_flight_leg(
                        end_loc,
                        supply,
                        end_time,
                        end_battery,
                        0.0,
                        map_data,
                        calculator,
                        predictor,
                        cruise,
                    )
                    if leg is None or t > SIMULATION_DURATION_SECONDS:
                        continue
                total_income = sum(
                    o.full_income
                    for o in group_orders
                    if o.weight_kg <= DRONE_MAX_PAYLOAD_KG
                )
                if total_income > best_group_score:
                    best_group_score = total_income
                    best_group = (supply, group_orders)

            if best_group is None:
                continue

            supply, group_orders = best_group

            # 构建批量行程
            legs_to_add = []
            current_loc = end_loc
            current_time = end_time
            current_battery = end_battery

            # 飞到供应地
            if current_loc != supply:
                leg, t, bat = _make_flight_leg(
                    current_loc,
                    supply,
                    current_time,
                    current_battery,
                    0.0,
                    map_data,
                    calculator,
                    predictor,
                    cruise,
                )
                if leg is None or t > SIMULATION_DURATION_SECONDS:
                    continue
                legs_to_add.append(leg)
                current_time = t
                current_battery = bat
                current_loc = supply

            # 选择可装载的订单（按收入密度排序）
            loadable_orders = sorted(
                [o for o in group_orders if o.is_deliverable_at(current_time + 600)],
                key=lambda o: -o.full_income / o.weight_kg,
            )

            loaded = []
            total_weight = 0.0
            for o in loadable_orders:
                if total_weight + o.weight_kg > DRONE_MAX_PAYLOAD_KG:
                    continue
                if o.generation_time_seconds > current_time:
                    current_time = o.generation_time_seconds
                loaded.append(o)
                total_weight += o.weight_kg

            if not loaded:
                continue

            # 装载（原地leg）
            load_leg = FlightLeg(
                from_location=current_loc,
                to_location=current_loc,
                load_orders=[o.order_id for o in loaded],
                depart_time=current_time,
                arrive_time=current_time,
                flight_distance_km=0.0,
                battery_before=current_battery,
                battery_after=current_battery,
            )
            legs_to_add.append(load_leg)

            # 按需求地分组，最近邻路线
            demand_map: Dict[str, List[Order]] = {}
            for o in loaded:
                demand_map.setdefault(o.demand_location, []).append(o)

            demand_locs = list(demand_map.keys())
            route = _nearest_neighbor_route(current_loc, demand_locs, map_data)

            # 依次送货
            payload = total_weight
            delivered_in_trip = []
            for dest in route:
                dest_orders = demand_map[dest]
                dest_payload = sum(o.weight_kg for o in dest_orders)

                leg, t, bat = _make_flight_leg(
                    current_loc,
                    dest,
                    current_time,
                    current_battery,
                    payload,
                    map_data,
                    calculator,
                    predictor,
                    cruise,
                )
                if leg is None or t > SIMULATION_DURATION_SECONDS or bat < 0:
                    payload -= dest_payload
                    continue

                leg.unload_orders = [o.order_id for o in dest_orders]
                legs_to_add.append(leg)
                delivered_in_trip.extend(dest_orders)
                for o in dest_orders:
                    payload -= o.weight_kg
                current_time = t
                current_battery = bat
                current_loc = dest

            if not delivered_in_trip:
                continue

            # 更新load_orders只保留实际送达的
            load_leg.load_orders = [o.order_id for o in delivered_in_trip]

            # 电量管理
            land_airports = [ap.name for ap in map_data.get_land_airports()]
            if (
                current_battery < BATTERY_RETURN_THRESHOLD
                and current_time < SIMULATION_DURATION_SECONDS - 300
            ):
                if current_loc in land_airports and legs_to_add:
                    legs_to_add[-1].swap_battery = True
                elif current_loc != "游轮":
                    try:
                        nearest, _ = map_data.find_nearest_land_airport(current_loc)
                        swap_leg, t_swap, bat_swap = _make_flight_leg(
                            current_loc,
                            nearest,
                            current_time,
                            current_battery,
                            0.0,
                            map_data,
                            calculator,
                            predictor,
                            cruise,
                        )
                        if (
                            swap_leg
                            and t_swap + DRONE_SWAP_TIME_SECONDS
                            < SIMULATION_DURATION_SECONDS
                        ):
                            swap_leg.swap_battery = True
                            legs_to_add.append(swap_leg)
                    except Exception:
                        pass

            # 评估
            trial = current_sol.deep_copy()
            for l in legs_to_add:
                trial.drone_plans[drone_id].legs.append(l)
            for o in delivered_in_trip:
                trial.unassigned_order_ids.discard(o.order_id)

            result = evaluator.evaluate(trial)
            if result.feasible and result.net_profit > current_profit:
                current_sol = trial
                current_profit = result.net_profit
                for o in delivered_in_trip:
                    inserted_ids.add(o.order_id)

        return current_sol


def _nearest_neighbor_route(
    from_loc: str, demands: List[str], map_data: MapData
) -> List[str]:
    """最近邻路线规划"""
    if len(demands) <= 1:
        return demands
    remaining = list(demands)
    route = []
    cur = from_loc
    while remaining:
        best = None
        best_d = float("inf")
        for d in remaining:
            dist = map_data.get_distance(cur, d)
            if dist is not None and dist < best_d:
                best_d = dist
                best = d
        if best is None:
            best = remaining[0]
        route.append(best)
        remaining.remove(best)
        cur = best
    return route


# ============================================================
# 工厂函数
# ============================================================


def create_repair_operators() -> List[RepairOperator]:
    """创建所有修复算子"""
    return [
        GreedyInsertion(),
        Regret2Insertion(),
        Regret3Insertion(),
        BatchNewTripInsertion(),
    ]
