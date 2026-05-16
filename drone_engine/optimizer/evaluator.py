"""
解评估器
========
模拟整个解的执行，计算精确的净利润。
这是ALNS最关键的模块——评估必须准确。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from functools import lru_cache

from ..map_data import MapData, GeoCoord, get_map_data, horizontal_distance_meters
from ..cruise_ship import (
    CruiseRoute,
    RendezvousPredictor,
    get_cruise_route,
    get_rendezvous_predictor,
)
from ..drone import (
    DroneFlightCalculator,
    DRONE_SPEED_MPS,
    DRONE_VERTICAL_SPEED_MPS,
    DRONE_BATTERY_DRAIN_RATE,
    DRONE_MAX_PAYLOAD_KG,
    DRONE_SWAP_TIME_SECONDS,
    DRONE_FLIGHT_ALTITUDE,
    DRONE_FIXED_COST,
    BATTERY_SWAP_COST,
    ACCIDENT_PENALTY,
    UNFULFILLED_PENALTY_PER_KG,
    DRONE_COUNT,
    SIMULATION_DURATION_SECONDS,
)
from ..order import OrderManager, Order
from .solution import Solution, DronePlan, FlightLeg


# ============================================================
# 评估结果
# ============================================================


@dataclass
class EvalResult:
    """评估结果"""

    feasible: bool = True
    net_profit: float = 0.0

    # 收入明细
    total_income: float = 0.0
    delivered_orders: Dict[int, float] = field(
        default_factory=dict
    )  # order_id → delivery_time

    # 成本明细
    total_fixed_cost: float = 0.0
    total_swap_cost: float = 0.0
    total_swap_count: int = 0
    total_crash_penalty: float = 0.0
    crash_count: int = 0
    total_unfulfilled_penalty: float = 0.0

    # 无人机终态
    drone_end_states: Dict[str, Dict] = field(default_factory=dict)

    # 违反约束
    violations: List[str] = field(default_factory=list)

    # 抢单池统计
    grab_pool_max_pending: int = 0
    grab_pool_events: List = field(default_factory=list)
    grab_pool_violating_order: Optional[int] = None

    def summary(self) -> str:
        lines = [
            f"净利润: {self.net_profit:.2f} 元  {'✅' if self.feasible else '❌'}",
            f"  收入: {self.total_income:.2f} 元 ({len(self.delivered_orders)}单)",
            f"  固定成本: -{self.total_fixed_cost:.2f} 元",
            f"  换电成本: -{self.total_swap_cost:.2f} 元 ({self.total_swap_count}次)",
            f"  坠毁惩罚: -{self.total_crash_penalty:.2f} 元 ({self.crash_count}次)",
            f"  未履约惩罚: -{self.total_unfulfilled_penalty:.2f} 元",
            f"  抢单池峰值: {self.grab_pool_max_pending}单",
        ]
        if self.violations:
            lines.append(f"  违反: {', '.join(self.violations)}")
        return "\n".join(lines)


# ============================================================
# 评估器
# ============================================================


class SolutionEvaluator:
    """
    解评估器
    ========
    模拟执行完整解，计算精确净利润。
    同时填充每个FlightLeg的时间、电量信息。
    """

    def __init__(
        self,
        order_manager: OrderManager,
        map_data: Optional[MapData] = None,
    ):
        self.order_manager = order_manager
        self.map_data = map_data or get_map_data()
        self.calculator = DroneFlightCalculator(self.map_data)
        self.cruise = get_cruise_route()
        self.predictor = get_rendezvous_predictor()
        self.land_airports = [ap.name for ap in self.map_data.get_land_airports()]
        self._eval_cache: Dict[int, Tuple[bool, float]] = {}

    def evaluate(self, solution: Solution) -> EvalResult:
        """
        评估完整解。

        逐架无人机模拟，计算收入、成本、惩罚。
        """
        result = EvalResult()
        all_delivered: Dict[int, float] = {}
        all_loaded: Set[int] = set()

        for drone_id, plan in solution.drone_plans.items():
            drone_result = self._evaluate_drone(plan)
            all_delivered.update(drone_result["delivered"])
            all_loaded.update(drone_result["loaded"])

            result.total_swap_count += drone_result["swap_count"]
            result.crash_count += drone_result["crash_count"]

            if not drone_result["feasible"]:
                result.feasible = False
                result.violations.extend(drone_result["violations"])

            result.drone_end_states[drone_id] = {
                "location": drone_result["end_location"],
                "battery": drone_result["end_battery"],
                "time": drone_result["end_time"],
                "total_distance_km": drone_result["total_distance_km"],
            }

        # 已送达订单的收入
        total_income = 0.0
        for order_id, delivery_time in all_delivered.items():
            if order_id in self.order_manager.orders:
                order = self.order_manager.orders[order_id]
                income = order.income_at_delivery(delivery_time)
                total_income += income

        result.delivered_orders = all_delivered
        result.total_income = total_income

        # 固定成本
        result.total_fixed_cost = DRONE_FIXED_COST * DRONE_COUNT

        # 换电成本
        result.total_swap_cost = result.total_swap_count * BATTERY_SWAP_COST

        # 坠毁惩罚
        result.total_crash_penalty = abs(ACCIDENT_PENALTY) * result.crash_count

        # 未履约惩罚：已分配但未送达的订单
        assigned = solution.assigned_order_ids()
        undelivered = assigned - set(all_delivered.keys())
        for order_id in undelivered:
            if order_id in self.order_manager.orders:
                order = self.order_manager.orders[order_id]
                result.total_unfulfilled_penalty += (
                    abs(UNFULFILLED_PENALTY_PER_KG) * order.weight_kg
                )

        # 净利润
        result.net_profit = (
            result.total_income
            - result.total_fixed_cost
            - result.total_swap_cost
            - result.total_crash_penalty
            - result.total_unfulfilled_penalty
        )

        # 抢单池动态约束检查
        self._check_grab_pool(result, solution, all_delivered)

        return result

    def _check_grab_pool(
        self, result: EvalResult, solution: Solution, all_delivered: Dict[int, float]
    ):
        """
        模拟抢单池：检查任意时刻待配送订单是否超过90单。

        策略：最优抢单时机 = 订单装车前一刻grab，
        这样最小化任意时刻的pending数量。

        事件模型：
          grab事件(+1): 在leg.depart_time时刻grab该leg装载的所有订单
          deliver事件(-1): 在leg.arrive_time时刻deliver该leg卸载的订单
          crash释放(-1): 坠毁时释放机上订单的抓取位置
        """
        MAX_PENDING = 90
        events = []

        # 收集所有无人机中坠毁的订单（需要释放grab位置）
        crashed_orders = set()
        for drone_id, plan in solution.drone_plans.items():
            drone_result = result.drone_end_states.get(drone_id, {})
            # 如果该无人机有坠毁，机上未卸载的订单需要释放
            # （通过drone_result无法直接获取，改用另一种方式）
            pass

        for drone_id, plan in solution.drone_plans.items():
            for leg in plan.legs:
                if not leg.load_orders:
                    continue

                grab_time = leg.depart_time
                for oid in leg.load_orders:
                    if oid in self.order_manager.orders:
                        order = self.order_manager.orders[oid]
                        effective_grab = max(grab_time, order.generation_time_seconds)
                        events.append((effective_grab, +1, oid))

                if not leg.unload_orders:
                    continue
                deliver_time = leg.arrive_time
                for oid in leg.unload_orders:
                    events.append((deliver_time, -1, oid))

        # 已分配但未送达的订单 = 坠毁或计划中断导致的丢失
        # 这些订单在仿真结束时释放grab位置
        assigned = solution.assigned_order_ids()
        undelivered = assigned - set(all_delivered.keys())
        for oid in undelivered:
            events.append((SIMULATION_DURATION_SECONDS, -1, oid))

        if not events:
            return

        events.sort(key=lambda e: (e[0], e[1]))

        pending = 0
        max_pending = 0
        violating_oid = None
        for time, delta, oid in events:
            pending += delta
            if pending > max_pending:
                max_pending = pending
            if pending > MAX_PENDING:
                result.feasible = False
                result.grab_pool_violating_order = oid
                result.violations.append(
                    f"抢单池超限: {pending}单 > {MAX_PENDING}单 (时刻{time:.0f}s)"
                )
                break

        result.grab_pool_max_pending = max_pending

    def _evaluate_drone(self, plan: DronePlan) -> Dict:
        """
        模拟一架无人机的计划执行。
        
        正确的执行逻辑：
        1. 按顺序处理每个leg
        2. 每个leg之前，需要先更新状态（当前位置、当前时间）
        3. 处理装货：检查订单生成时间，等待订单生成
        4. 执行飞行：计算飞行时间、电量消耗
        5. 处理卸货：在到达时刻卸载订单
        
        返回字典包含:
          feasible, delivered, loaded, swap_count, crash_count,
          end_location, end_battery, end_time, total_distance_km, violations
        """
        battery = 100.0
        location = plan.initial_location
        current_time = 0.0
        payload = 0.0
        onboard: Dict[int, Order] = {}
        delivered: Dict[int, float] = {}
        loaded: Set[int] = set()
        swap_count = 0
        crash_count = 0
        total_distance = 0.0
        violations = []
        feasible = True
        
        for leg in plan.legs:
            # ========== 阶段1: 飞行前准备 ==========
            
            # 更新leg的起始状态
            leg.depart_time = current_time
            leg.battery_before = battery
            
            # ========== 阶段2: 处理装货 ==========
            for oid in leg.load_orders:
                if oid in self.order_manager.orders:
                    order = self.order_manager.orders[oid]
                    if order.generation_time_seconds > current_time:
                        current_time = order.generation_time_seconds
                    payload += order.weight_kg
                    onboard[oid] = order
                    loaded.add(oid)
            
            # 载重检查
            if payload > DRONE_MAX_PAYLOAD_KG:
                violations.append(
                    f"载重超限: {payload:.1f}kg > {DRONE_MAX_PAYLOAD_KG}kg"
                )
                feasible = False
            
            # ========== 阶段3: 原地leg处理（换电或无飞行） ==========
            if leg.from_location == leg.to_location and leg.flight_distance_km == 0:
                if leg.swap_battery:
                    if location in self.land_airports:
                        battery = 100.0
                        current_time += DRONE_SWAP_TIME_SECONDS
                        swap_count += 1
                        leg.battery_after = 100.0
                    else:
                        violations.append(f"换电只能在陆地机场: {location} 不可换电")
                        feasible = False
                        leg.battery_after = battery
                else:
                    leg.battery_after = battery
                leg.arrive_time = current_time
                continue
            
            # ========== 阶段4: 执行飞行 ==========
            to_loc = leg.to_location
            is_cruise_departure = location == "游轮"
            is_cruise_target = to_loc == "游轮" or leg.cruise_target is not None
            
            try:
                if is_cruise_departure and not is_cruise_target:
                    cruise_pos = self.cruise.position_at_time(current_time)
                    flight_info = self.calculator.fly_from_cruise(
                        to_loc, current_time, cruise_pos, battery, payload
                    )
                    arrive_time = flight_info["arrival_time_seconds"]
                    flight_dist = flight_info["total_distance_km"]
                    battery_after = flight_info["battery_remaining"]
                elif is_cruise_target:
                    flight_info = self._compute_flight_to_cruise(
                        location, current_time, battery, payload
                    )
                    if not flight_info.get("feasible", True):
                        violations.append(f"无法飞往游轮: {location}")
                        feasible = False
                        crash_count += 1
                        break
                    leg.cruise_target = flight_info["rendezvous"]
                    arrive_time = flight_info["arrive_time"]
                    flight_dist = flight_info["distance_km"]
                    battery_after = flight_info["battery_after"]
                else:
                    flight_info = self.calculator.fly_between_nodes(
                        location, to_loc, battery, payload
                    )
                    arrive_time = current_time + flight_info["total_time_seconds"]
                    flight_dist = flight_info["total_distance_km"]
                    battery_after = flight_info["battery_remaining"]
            except ValueError:
                violations.append(f"无航线: {location} → {to_loc}")
                feasible = False
                crash_count += 1
                break
            
            if battery_after < 0:
                violations.append(
                    f"电量不足: {location} → {to_loc}, 消耗{battery - battery_after:.1f}%, 剩余{battery:.1f}%"
                )
                feasible = False
                crash_count += 1
                break
            
            if arrive_time > SIMULATION_DURATION_SECONDS:
                violations.append(
                    f"超时: 到达{to_loc}时刻{arrive_time:.0f}s > {SIMULATION_DURATION_SECONDS:.0f}s"
                )
                feasible = False
                break
            
            # 更新状态
            current_time = arrive_time
            battery = battery_after
            location = to_loc
            total_distance += flight_dist
            
            leg.arrive_time = arrive_time
            leg.flight_distance_km = flight_dist
            leg.battery_after = battery
            
            # ========== 阶段5: 处理卸货 ==========
            for oid in leg.unload_orders:
                if oid in onboard:
                    delivered[oid] = current_time
                    del onboard[oid]
            payload = sum(o.weight_kg for o in onboard.values())
            
            # ========== 阶段6: 换电处理 ==========
            if leg.swap_battery:
                if location in self.land_airports:
                    current_time += DRONE_SWAP_TIME_SECONDS
                    battery = 100.0
                    swap_count += 1
                else:
                    violations.append(f"换电只能在陆地机场: {location} 不可换电")
                    feasible = False
        
        return {
            "feasible": feasible,
            "delivered": delivered,
            "loaded": loaded,
            "swap_count": swap_count,
            "crash_count": crash_count,
            "end_location": location,
            "end_battery": battery,
            "end_time": current_time,
            "total_distance_km": total_distance,
            "violations": violations,
        }

    def _compute_flight_to_cruise(
        self, from_node: str, depart_time: float, battery: float, payload: float
    ) -> Dict:
        """计算飞往游轮的飞行信息"""
        # 游轮→游轮的情况不应发生，但防御性处理
        if from_node == "游轮":
            return {
                "feasible": False,
                "error": "Cannot fly from cruise to cruise",
            }

        # 如果从非机场节点起飞，需要先找到机场数据
        # 渔船没有机场数据，但可以从渔船起飞
        try:
            airport = self.map_data.get_airport(from_node)
        except KeyError:
            # from_node可能是游轮或渔船（没有airport数据）
            # 使用默认垂直起飞距离
            takeoff_dist_km = DRONE_FLIGHT_ALTITUDE / 1000.0
            takeoff_time = DRONE_FLIGHT_ALTITUDE / DRONE_VERTICAL_SPEED_MPS
            # 计算飞行
            rendezvous, arrival_time, flight_dist = (
                self.predictor.compute_rendezvous_from_node(
                    from_node, depart_time, self.map_data
                )
            )
            landing_alt_diff = max(
                0, DRONE_FLIGHT_ALTITUDE - self.cruise.WAYPOINTS[0].alt
            )
            landing_dist_km = landing_alt_diff / 1000.0
            landing_time = landing_alt_diff / DRONE_VERTICAL_SPEED_MPS
            total_dist = flight_dist + takeoff_dist_km + landing_dist_km
            total_time = takeoff_time + (arrival_time - depart_time) + landing_time
            battery_consumed = self.calculator.battery_consumption(total_dist)
            battery_remaining = battery - battery_consumed
            return {
                "feasible": battery_remaining >= 0,
                "arrive_time": depart_time + total_time,
                "distance_km": total_dist,
                "battery_after": battery_remaining,
                "battery_consumed": battery_consumed,
                "rendezvous": rendezvous,
            }

        # 预测汇合点
        rendezvous, arrival_time, flight_dist = (
            self.predictor.compute_rendezvous_from_node(
                from_node, depart_time, self.map_data
            )
        )

        # 垂直降落距离
        landing_alt_diff = max(0, DRONE_FLIGHT_ALTITUDE - self.cruise.WAYPOINTS[0].alt)
        landing_dist_km = landing_alt_diff / 1000.0
        landing_time = landing_alt_diff / DRONE_VERTICAL_SPEED_MPS

        total_dist = flight_dist + landing_dist_km
        total_time = (arrival_time - depart_time) + landing_time

        battery_consumed = self.calculator.battery_consumption(total_dist)
        battery_after = battery - battery_consumed

        return {
            "rendezvous": rendezvous,
            "arrive_time": depart_time + total_time,
            "distance_km": total_dist,
            "battery_after": battery_after,
        }

    def compute_order_score(self, order: Order, current_time: float = 0.0) -> float:
        """
        计算订单的可配送价值评分。
        用于初始解构造和抢单决策。
        """
        supply = order.supply_location
        demand = order.demand_location

        # 1. 飞行距离估算
        if demand == "游轮":
            # 游轮距离估算：从供应地到最近航路点的平均距离
            dist_estimate = 5.0
        elif supply == "游轮":
            dist_estimate = 5.0
        else:
            dist = self.map_data.get_distance(supply, demand)
            if dist is None:
                try:
                    dist, _ = self.map_data.find_shortest_path(supply, demand)
                except ValueError:
                    return -999.0  # 不可达
            dist_estimate = dist

        # 2. 往返距离估算（含垂直起降）
        # 从陆地机场出发 → 供应地 → 需求地 → 回陆地
        # 粗估：单程距离 × 2.2（含起降和可能的绕路）
        round_trip_dist = dist_estimate * 2.5

        # 3. 飞行时间估算
        flight_time_min = round_trip_dist / (DRONE_SPEED_MPS / 1000 * 60)  # 分钟

        # 4. 电量需求
        battery_needed = round_trip_dist * DRONE_BATTERY_DRAIN_RATE

        # 5. 换电需求
        swap_needed = battery_needed > 100
        swap_time_min = 3.0 if swap_needed else 0.0
        swap_cost = BATTERY_SWAP_COST if swap_needed else 0.0

        # 6. 时间可行性
        total_time_min = flight_time_min + swap_time_min
        time_available = order.time_window_minutes

        if total_time_min > time_available * 2:
            # 飞行时间远超时间窗 → 几乎不可能按时
            time_feasibility = 0.1
        elif total_time_min > time_available:
            # 超时窗但可能还有部分收入
            time_feasibility = 0.4
        else:
            # 余量比例
            margin = (time_available - total_time_min) / time_available
            time_feasibility = min(1.0, 0.5 + margin)

        # 7. 预计收入
        expected_income = order.full_income * time_feasibility

        # 8. 风险惩罚
        risk_penalty = (
            abs(UNFULFILLED_PENALTY_PER_KG) * order.weight_kg * (1 - time_feasibility)
        )

        # 9. 飞行成本等价
        flight_cost = swap_cost + round_trip_dist * 0.5  # 粗估每公里0.5元成本

        # 10. 最终评分
        score = expected_income - flight_cost - risk_penalty
        return score

    def quick_evaluate(self, solution: Solution) -> Tuple[bool, float]:
        """
        快速评估：只返回是否可行和净利润，不做完整模拟。
        用于修复算子中大量候选解的快速筛选。
        """
        solution_hash = hash(solution.assigned_order_ids())
        if solution_hash in self._eval_cache:
            return self._eval_cache[solution_hash]

        result = self.evaluate(solution)
        self._eval_cache[solution_hash] = (result.feasible, result.net_profit)
        return (result.feasible, result.net_profit)

    def clear_cache(self):
        """清除评估缓存"""
        self._eval_cache.clear()
