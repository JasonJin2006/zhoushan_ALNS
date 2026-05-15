"""
初始解构造 v6
=============
策略：每架无人机持续执行 "飞到供应地取货 → 送货 → 如需换电则飞回陆地换电" 循环
关键：不预留返程电量，而是让调度循环在电量低时主动飞回陆地换电
改进：自动枚举所有无人机-机场分配方案，选择净利润最优的
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from itertools import product

from ..map_data import MapData, get_map_data
from ..cruise_ship import get_cruise_route, get_rendezvous_predictor
from ..drone import (
    DroneFlightCalculator,
    DRONE_MAX_PAYLOAD_KG,
    DRONE_SWAP_TIME_SECONDS,
    DRONE_FLIGHT_ALTITUDE,
    DRONE_VERTICAL_SPEED_MPS,
    DRONE_SPEED_MPS,
    DRONE_BATTERY_DRAIN_RATE,
    SIMULATION_DURATION_SECONDS,
)
from ..order import Order, OrderManager
from .solution import Solution, DronePlan, FlightLeg
from .evaluator import SolutionEvaluator
from .config import ALNSConfig


# 全局电量安全阈值
BATTERY_SAFETY_MARGIN = 5.0  # 电量安全余量百分比
BATTERY_RETURN_THRESHOLD = 15.0  # 电量低于此值时需返回陆地
BATTERY_SWAP_THRESHOLD = 15.0  # 电量低于此值时建议换电


@dataclass
class DroneState:
    drone_id: str
    location: str
    battery: float = 100.0
    available_time: float = 0.0
    swap_count: int = 0


class GreedyConstructor:
    def __init__(self, order_manager, map_data=None, config=None):
        self.order_manager = order_manager
        self.map_data = map_data or get_map_data()
        self.config = config or ALNSConfig()
        self.calculator = DroneFlightCalculator(self.map_data)
        self.cruise = get_cruise_route()
        self.predictor = get_rendezvous_predictor()
        self.evaluator = SolutionEvaluator(order_manager, self.map_data)
        self.land_airports = [ap.name for ap in self.map_data.get_land_airports()]

    def construct(self) -> Solution:
        drone_ids = list(self.config.initial_positions.keys())
        
        if len(drone_ids) <= 1 or len(self.land_airports) <= 1:
            return self._construct_with_fixed_positions()
        
        best_solution = None
        best_profit = float("-inf")
        best_assignment = None
        
        total = len(self.land_airports) ** len(drone_ids)
        
        for combo in product(self.land_airports, repeat=len(drone_ids)):
            assignment = {drone_ids[i]: combo[i] for i in range(len(drone_ids))}
            solution = self._construct_with_assignment(assignment)
            result = self.evaluator.evaluate(solution)
            profit = result.net_profit if result.feasible else result.net_profit - 1e9
            
            if profit > best_profit:
                best_profit = profit
                best_solution = solution
                best_assignment = assignment
        
        if self.config.verbose:
            assignment_str = ", ".join([f"{k}@{v}" for k, v in best_assignment.items()])
            print(f"    [初始位置优化] 枚举{total}种分配，选择: {assignment_str}, 利润: {best_profit:.2f}")
        
        return best_solution or self._construct_with_fixed_positions()

    def _construct_with_fixed_positions(self) -> Solution:
        """使用配置中的固定位置构造"""
        states, plans = {}, {}
        for did, pos in self.config.initial_positions.items():
            states[did] = DroneState(drone_id=did, location=pos)
            plans[did] = DronePlan(drone_id=did, initial_location=pos)

        candidates = set(self.order_manager.orders.keys())
        assigned: Set[int] = set()
        sim_end = SIMULATION_DURATION_SECONDS

        for did in states:
            self._schedule_drone(states[did], plans[did], candidates, assigned, sim_end)

        solution = Solution(
            drone_plans=plans,
            unassigned_order_ids=set(self.order_manager.orders.keys()) - assigned,
        )
        return solution

    def _construct_with_assignment(self, assignment: Dict[str, str]) -> Solution:
        """使用指定的分配方案构造"""
        states, plans = {}, {}
        for did, pos in assignment.items():
            states[did] = DroneState(drone_id=did, location=pos)
            plans[did] = DronePlan(drone_id=did, initial_location=pos)

        candidates = set(self.order_manager.orders.keys())
        assigned: Set[int] = set()
        sim_end = SIMULATION_DURATION_SECONDS

        for did in states:
            self._schedule_drone(states[did], plans[did], candidates, assigned, sim_end)

        solution = Solution(
            drone_plans=plans,
            unassigned_order_ids=set(self.order_manager.orders.keys()) - assigned,
        )
        return solution

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  持续调度一架无人机
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _schedule_drone(self, state, plan, candidates, assigned, sim_end):
        for _ in range(200):
            if state.available_time >= sim_end - 30:
                break

            # 1) 如果不在陆地且电量不够飞回陆地 → 必须立刻回陆地换电
            if state.location not in self.land_airports:
                nearest = self._nearest_land(state.location)
                ret_est = self._estimate_flight(
                    state.location, nearest, state.battery, 0.0
                )
                if ret_est and state.battery - ret_est[1] < BATTERY_SAFETY_MARGIN:
                    if not self._return_and_swap(state, plan, sim_end):
                        break
                    continue

            # 2) 如果电量低且不在陆地 → 飞回陆地换电
            if state.battery < BATTERY_RETURN_THRESHOLD and state.location not in self.land_airports:
                if not self._return_and_swap(state, plan, sim_end):
                    break
                continue

            # 3) 如果在陆地且电量低 → 换电（更积极）
            if (
                state.battery < BATTERY_SWAP_THRESHOLD
                and state.location in self.land_airports
                and (sim_end - state.available_time) > 600
            ):
                self._swap_at_current(state, plan)
                continue

            # 4) 尝试构造一趟配送
            legs, trip_ids = self._build_trip(state, candidates, assigned, sim_end)
            if legs and trip_ids:
                for leg in legs:
                    plan.legs.append(leg)
                for oid in trip_ids:
                    assigned.add(oid)
                    candidates.discard(oid)
                last = legs[-1]
                state.location = last.to_location
                if last.swap_battery:
                    state.battery = 100.0
                    state.available_time = last.arrive_time + DRONE_SWAP_TIME_SECONDS
                    state.swap_count += 1
                else:
                    state.battery = last.battery_after
                    state.available_time = last.arrive_time
                continue

            # 5) 没订单 → 等新订单
            next_gen = self._next_order_time(state, candidates, assigned)
            if (
                next_gen
                and next_gen < sim_end
                and next_gen - state.available_time < 180
            ):
                state.available_time = next_gen
                continue

            break

    def _return_and_swap(self, state, plan, sim_end):
        """飞回最近陆地并换电"""
        nearest = self._nearest_land(state.location)
        leg, t_new, bat_new, loc_new = self._fly(
            state.location, nearest, state.available_time, state.battery, 0.0
        )
        if leg is None or t_new > sim_end:
            return False
        leg.swap_battery = True
        plan.legs.append(leg)
        state.battery = 100.0
        state.available_time = t_new + DRONE_SWAP_TIME_SECONDS
        state.location = nearest
        state.swap_count += 1
        return True

    def _swap_at_current(self, state, plan):
        """在当前陆地机场换电"""
        plan.legs.append(
            FlightLeg(
                from_location=state.location,
                to_location=state.location,
                swap_battery=True,
                depart_time=state.available_time,
                arrive_time=state.available_time,
                battery_before=state.battery,
                battery_after=100.0,
            )
        )
        state.battery = 100.0
        state.available_time += DRONE_SWAP_TIME_SECONDS
        state.swap_count += 1

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  构造一趟配送
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _build_trip(self, state, candidates, assigned, sim_end):
        t, bat, loc = state.available_time, state.battery, state.location
        if sim_end - t < 60:
            return [], set()

        # 安全约束：电量不足以飞回陆地 → 不配送
        if bat < 15 and loc not in self.land_airports:
            return [], set()
        
        # 预留返程电量：确保电量够飞回最近陆地
        if loc not in self.land_airports:
            nearest = self._nearest_land(loc)
            ret_est = self._estimate_flight(loc, nearest, bat, 0.0)
            if ret_est and bat - ret_est[1] < BATTERY_SAFETY_MARGIN:
                return [], set()

        # 找可配送订单
        available = self._get_available_orders(t, candidates, assigned)
        if not available:
            return [], set()

        # 按供应地分组
        supply_groups: Dict[str, List[Order]] = {}
        for order in available:
            supply_groups.setdefault(order.supply_location, []).append(order)

        # 选最优供应地
        best_supply = self._pick_best_supply(loc, bat, t, supply_groups, sim_end)
        if best_supply is None:
            return [], set()

        legs, trip_ids = [], set()
        current_payload = 0.0

        # Phase 1: 飞到供应地
        if loc != best_supply:
            leg, t, bat, loc = self._fly(loc, best_supply, t, bat, current_payload)
            if leg is None or bat < 0:
                return [], set()
            legs.append(leg)

        # Phase 2: 装载订单
        loaded = self._select_loadable_orders(
            supply_groups[best_supply],
            t,
            DRONE_MAX_PAYLOAD_KG,
        )
        if not loaded:
            return [], set()

        load_ids = [o.order_id for o in loaded]
        current_payload = sum(o.weight_kg for o in loaded)

        # Phase 3: 送货路线规划
        demand_map: Dict[str, List[Order]] = {}
        for o in loaded:
            demand_map.setdefault(o.demand_location, []).append(o)
        demand_locs = list(demand_map.keys())
        route = self._nn_route(loc, demand_locs)
        if len(demand_locs) > 2:
            route = self._two_opt_route(loc, demand_locs)

        # Phase 4: 依次送货（第一个leg记录装载的订单）
        first_leg = True
        for dest in route:
            dest_orders = demand_map[dest]

            # 飞往目的地
            leg, t_new, bat_new, loc_new = self._fly(loc, dest, t, bat, current_payload)
            if leg is None or t_new > sim_end or bat_new < 0:
                return legs, trip_ids

            # 第一个leg记录装载的订单
            if first_leg:
                leg.load_orders = load_ids
                first_leg = False

            # 设置卸货订单
            leg.unload_orders = [o.order_id for o in dest_orders]
            legs.append(leg)

            for o in dest_orders:
                trip_ids.add(o.order_id)

            current_payload -= sum(o.weight_kg for o in dest_orders)
            t, bat, loc = t_new, bat_new, loc_new

            # 送货后检查：当前位置是否能飞回陆地
            if loc not in self.land_airports and bat > 0:
                nearest = self._nearest_land(loc)
                ret_est = self._estimate_flight(loc, nearest, bat, 0.0)
                if ret_est and bat - ret_est[1] < BATTERY_SAFETY_MARGIN:
                    break

        # Phase 5: 电量管理（换电）
        if bat < BATTERY_RETURN_THRESHOLD and loc not in self.land_airports:
            nearest = self._nearest_land(loc)
            leg, t_new, bat_new, loc_new = self._fly(loc, nearest, t, bat, current_payload)
            if leg and t_new < sim_end and bat_new >= 0:
                leg.swap_battery = True
                legs.append(leg)
                t, bat, loc = t_new, bat_new, loc_new

        elif bat < BATTERY_SWAP_THRESHOLD and loc in self.land_airports and (sim_end - t) > 600:
            # 在当前陆地机场换电：不需要额外的leg，只需要更新状态
            t += DRONE_SWAP_TIME_SECONDS
            bat = 100.0
            state.available_time = t
            state.swap_count += 1

        return legs, trip_ids

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  选最优供应地
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _pick_best_supply(self, loc, bat, t, supply_groups, sim_end):
        best, best_score = None, -1
        for supply, orders in supply_groups.items():
            # 估算飞到供应地
            if loc == supply:
                fly_time, fly_bat = 0.0, 0.0
            else:
                est = self._estimate_flight(loc, supply, bat, 0.0)
                if est is None:
                    continue
                fly_time, fly_bat = est
                if bat - fly_bat < 0:
                    continue
                if t + fly_time > sim_end - 120:
                    continue

            # 可装载订单的总收入
            loadable = self._select_loadable_orders(orders, t + fly_time)
            if not loadable:
                continue
            total_income = sum(o.full_income for o in loadable)
            score = total_income / (fly_time / 60 + 1)

            if score > best_score:
                best_score = score
                best = supply
        return best

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  飞行计算
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _fly(self, from_loc, to_loc, t, bat, payload):
        """计算飞行leg，返回 (leg, arrive_time, battery_after, new_loc) 或 (None,...)"""
        if from_loc == to_loc:
            return None, 0, 0, from_loc

        is_cruise_from = from_loc == "游轮"
        is_cruise_to = to_loc == "游轮"

        if is_cruise_from and not is_cruise_to:
            try:
                cruise_pos = self.cruise.position_at_time(t)
                info = self.calculator.fly_from_cruise(
                    to_loc, t, cruise_pos, bat, payload
                )
                ba = info["battery_remaining"]
                if ba < 0:
                    return None, 0, 0, from_loc
                leg = FlightLeg(
                    from_location=from_loc,
                    to_location=to_loc,
                    depart_time=t,
                    arrive_time=info["arrival_time_seconds"],
                    flight_distance_km=info["total_distance_km"],
                    battery_before=bat,
                    battery_after=ba,
                )
                return leg, info["arrival_time_seconds"], ba, to_loc
            except:
                return None, 0, 0, from_loc

        elif is_cruise_to:
            try:
                rendezvous, arrival, flight_dist = (
                    self.predictor.compute_rendezvous_from_node(
                        from_loc, t, self.map_data
                    )
                )
                landing_alt = max(0, DRONE_FLIGHT_ALTITUDE - 15.0)
                landing_dist = landing_alt / 1000.0
                landing_time = landing_alt / DRONE_VERTICAL_SPEED_MPS
                total_dist = flight_dist + landing_dist
                total_time = (arrival - t) + landing_time
                bat_consumed = self.calculator.battery_consumption(total_dist)
                ba = bat - bat_consumed
                if ba < 0:
                    return None, 0, 0, from_loc
                arrive = t + total_time
                leg = FlightLeg(
                    from_location=from_loc,
                    to_location=to_loc,
                    cruise_target=rendezvous,
                    depart_time=t,
                    arrive_time=arrive,
                    flight_distance_km=total_dist,
                    battery_before=bat,
                    battery_after=ba,
                )
                return leg, arrive, ba, to_loc
            except:
                return None, 0, 0, from_loc

        else:
            try:
                info = self.calculator.fly_between_nodes(from_loc, to_loc, bat, payload)
                if not info["feasible"]:
                    return None, 0, 0, from_loc
                ba = info["battery_remaining"]
                if ba < 0:
                    return None, 0, 0, from_loc
                arrive = t + info["total_time_seconds"]
                leg = FlightLeg(
                    from_location=from_loc,
                    to_location=to_loc,
                    depart_time=t,
                    arrive_time=arrive,
                    flight_distance_km=info["total_distance_km"],
                    battery_before=bat,
                    battery_after=ba,
                )
                return leg, arrive, ba, to_loc
            except:
                return None, 0, 0, from_loc

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  辅助
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _get_available_orders(self, t, candidates, assigned):
        result = []
        for oid in candidates:
            if oid in assigned:
                continue
            order = self.order_manager.orders.get(oid)
            if not order:
                continue
            if order.generation_time_seconds > t + 300:
                continue
            if not order.is_deliverable_at(t + 600):
                continue
            score = self.evaluator.compute_order_score(order, t)
            if score > 0:
                result.append(order)
        result.sort(key=lambda o: -o.full_income / o.weight_kg)
        return result[:90]

    def _select_loadable_orders(
        self, orders, pickup_time, max_weight=DRONE_MAX_PAYLOAD_KG
    ):
        loaded, weight = [], 0.0
        for o in orders:
            if weight + o.weight_kg > max_weight:
                continue
            if not o.is_deliverable_at(pickup_time + 600):
                continue
            loaded.append(o)
            weight += o.weight_kg
        return loaded

    def _estimate_flight(self, from_loc, to_loc, bat, payload):
        if from_loc == to_loc:
            return (0.0, 0.0)
        if to_loc == "游轮" or from_loc == "游轮":
            dist = self.map_data.get_distance(
                from_loc if from_loc != "游轮" else "测试基地L1",
                to_loc if to_loc != "游轮" else "测试基地L1",
            )
            if dist is None:
                return (300.0, bat * 0.12)
            # 游轮方向加~50%绕路
            time_est = dist * 1000 / DRONE_SPEED_MPS * 1.5
            bat_est = dist * DRONE_BATTERY_DRAIN_RATE * 1.5
            return (time_est, bat_est)
        try:
            info = self.calculator.fly_between_nodes(from_loc, to_loc, bat, payload)
            if not info["feasible"]:
                return None
            return (info["total_time_seconds"], info["battery_consumed"])
        except ValueError:
            return None

    def _nearest_land(self, loc):
        try:
            name, _ = self.map_data.find_nearest_land_airport(loc)
            return name
        except:
            return self.land_airports[0]

    def _nn_route(self, from_loc, demands):
        if len(demands) <= 1:
            return demands
        remaining = list(demands)
        route, cur = [], from_loc
        while remaining:
            best, best_d = None, float("inf")
            for d in remaining:
                dist = self.map_data.get_distance(cur, d)
                if dist is not None and dist < best_d:
                    best_d = dist
                    best = d
            if best is None:
                best = remaining[0]
            route.append(best)
            remaining.remove(best)
            cur = best
        return route

    def _two_opt_route(self, from_loc, demands):
        """2-opt 路线优化: 尝试反转子路径以缩短总距离"""
        if len(demands) <= 2:
            return self._nn_route(from_loc, demands)

        route = self._nn_route(from_loc, demands)

        def route_distance(r):
            total = 0.0
            prev = from_loc
            for d in r:
                d_ = self.map_data.get_distance(prev, d)
                if d_:
                    total += d_
                prev = d
            return total

        improved = True
        while improved:
            improved = False
            best_dist = route_distance(route)
            for i in range(len(route)):
                for j in range(i + 2, len(route)):
                    new_route = route[:i] + route[i : j + 1][::-1] + route[j + 1 :]
                    new_dist = route_distance(new_route)
                    if new_dist < best_dist:
                        route = new_route
                        best_dist = new_dist
                        improved = True
                        break
                if improved:
                    break
        return route

    def _next_order_time(self, state, candidates, assigned):
        best = None
        for oid in candidates:
            if oid in assigned:
                continue
            order = self.order_manager.orders.get(oid)
            if not order:
                continue
            gt = order.generation_time_seconds
            if gt > state.available_time:
                if best is None or gt < best:
                    best = gt
        return best
