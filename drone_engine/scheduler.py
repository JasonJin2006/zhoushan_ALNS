"""
无人机调度器（ALNS版）
====================
基于自适应大邻域搜索的无人机配送调度器。
替代旧版贪心调度器。
"""

import time
from typing import Dict, List, Optional

from .map_data import MapData, get_map_data
from .order import OrderManager
from .cost import CostCalculator, ProfitResult, BATTERY_SWAP_COST
from .flight_plan import FlightPlan, FlightPlanBuilder, FlightAction, ActionType
from .optimizer import ALNSConfig, ALNSEngine, SolutionEvaluator, Solution, EvalResult


# ============================================================
# 调度器
# ============================================================

class DroneScheduler:
    """
    无人机调度器（ALNS版）
    =====================
    使用ALNS算法为3架无人机生成180分钟飞行计划。
    """

    def __init__(
        self,
        map_data: Optional[MapData] = None,
        order_manager: Optional[OrderManager] = None,
        config: Optional[ALNSConfig] = None,
    ):
        self.map_data = map_data or get_map_data()
        self.order_manager = order_manager or OrderManager()
        self.config = config or ALNSConfig()

    def schedule(
        self,
        initial_positions: Optional[Dict[str, str]] = None,
    ) -> Dict:
        """
        执行调度，生成3架无人机的完整飞行计划。

        参数:
            initial_positions: 各无人机初始位置（可选，默认用config中的配置）

        返回:
            调度结果字典
        """
        if initial_positions:
            self.config.initial_positions = initial_positions

        # 1. 运行ALNS
        engine = ALNSEngine(
            self.order_manager, self.map_data, self.config
        )
        solution = engine.solve()

        # 2. 评估最优解
        evaluator = SolutionEvaluator(self.order_manager, self.map_data)
        eval_result = evaluator.evaluate(solution)

        # 3. 转换为输出格式
        plan = self._solution_to_flight_plan(solution)

        # 4. 构建结果字典
        result = self._build_result(solution, eval_result, plan)

        return result

    def _solution_to_flight_plan(self, solution: Solution) -> FlightPlan:
        """
        将ALNS解转换为FlightPlan（用于导出Excel）。

        FlightLeg语义：
        - 纯移动leg：有实际飞行（from != to），但无装卸货
        - 取送货leg：有实际飞行（from != to），且有装卸货
        - 禁止：from == to 的leg（同一地点起飞又降落，毫无意义）

        每个有实际飞行的leg都需要添加起飞和降落动作。
        """
        builder = FlightPlanBuilder(self.map_data, self.order_manager)

        for drone_id, drone_plan in solution.drone_plans.items():
            builder.init_drone(drone_id, drone_plan.initial_location)

            for leg in drone_plan.legs:
                # 跳过没有实际飞行的leg（from == to），这些是毫无意义的
                if leg.from_location == leg.to_location:
                    continue

                # 添加起飞动作
                builder.add_takeoff(
                    drone_id=drone_id,
                    time_seconds=leg.depart_time,
                    location=leg.from_location,
                    load_order_ids=leg.load_orders if leg.load_orders else None,
                    target_cruise_coord=leg.cruise_target,
                )

                # 添加降落动作
                builder.add_landing(
                    drone_id=drone_id,
                    time_seconds=leg.arrive_time,
                    location=leg.to_location,
                    unload_order_ids=leg.unload_orders if leg.unload_orders else None,
                    swap_battery=leg.swap_battery,
                )

        return builder.get_combined_plan()

    def _build_result(
        self, solution: Solution, eval_result: EvalResult, plan: FlightPlan
    ) -> Dict:
        """构建调度结果字典（兼容run_scheduler.py的输出格式）"""
        # 各无人机统计
        drone_stats = {}
        for drone_id, drone_plan in solution.drone_plans.items():
            assigned = drone_plan.assigned_order_ids()
            drone_income = 0.0
            for oid in assigned:
                if oid in eval_result.delivered_orders:
                    order = self.order_manager.orders[oid]
                    income = order.income_at_delivery(eval_result.delivered_orders[oid])
                    drone_income += income

            drone_stats[drone_id] = {
                'total_deliveries': len(assigned),
                'total_income': drone_income,
                'total_distance_km': drone_plan.total_distance_km,
                'total_swaps': drone_plan.total_swap_count,
                'swap_cost': drone_plan.total_swap_count * BATTERY_SWAP_COST,
                'final_location': drone_plan.last_location,
                'final_battery': eval_result.drone_end_states.get(drone_id, {}).get('battery', 0),
            }

        actions = {drone_id: [] for drone_id in solution.drone_plans}

        for drone_id, drone_plan in solution.drone_plans.items():
            current_payload = 0.0
            current_battery = 100.0
            current_location = drone_plan.initial_location

            for leg in drone_plan.legs:
                # 跳过没有实际飞行的leg（from == to），这些是毫无意义的
                if leg.from_location == leg.to_location:
                    continue

                load_weight = sum(
                    self.order_manager.orders[oid].weight_kg
                    for oid in leg.load_orders
                    if oid in self.order_manager.orders
                )
                unload_weight = sum(
                    self.order_manager.orders[oid].weight_kg
                    for oid in leg.unload_orders
                    if oid in self.order_manager.orders
                )

                # 添加起飞动作
                actions[drone_id].append({
                    'drone_id': drone_id,
                    'time_seconds': leg.depart_time,
                    'action_type': 'takeoff',
                    'location': leg.from_location,
                    'load_orders': leg.load_orders,
                    'unload_orders': [],
                    'swap_battery': False,
                    'target_cruise_coord': leg.cruise_target,
                    'flight_distance_km': leg.flight_distance_km,
                    'battery_before': leg.battery_before,
                    'battery_after': leg.battery_before,
                    'payload_before': current_payload,
                    'payload_after': current_payload + load_weight,
                    'remark': '',
                })
                current_payload += load_weight

                # 添加降落动作
                actions[drone_id].append({
                    'drone_id': drone_id,
                    'time_seconds': leg.arrive_time,
                    'action_type': 'landing',
                    'location': leg.to_location,
                    'load_orders': [],
                    'unload_orders': leg.unload_orders,
                    'swap_battery': leg.swap_battery,
                    'target_cruise_coord': None,
                    'flight_distance_km': leg.flight_distance_km,
                    'battery_before': leg.battery_before,
                    'battery_after': leg.battery_after,
                    'payload_before': current_payload,
                    'payload_after': current_payload - unload_weight,
                    'remark': '',
                })
                current_payload -= unload_weight
                current_location = leg.to_location

                # 更新状态
                if leg.is_flying:
                    current_battery = leg.battery_after
                    if leg.swap_battery:
                        current_battery = 100.0
                elif leg.swap_battery:
                    current_battery = 100.0

        result = {
            'total_orders': len(self.order_manager.orders),
            'delivered_orders': len(eval_result.delivered_orders),
            'total_income': eval_result.total_income,
            'total_fixed_cost': eval_result.total_fixed_cost,
            'total_swap_cost': eval_result.total_swap_cost,
            'total_swaps': eval_result.total_swap_count,
            'total_crash_penalty': eval_result.total_crash_penalty,
            'unfulfilled_penalty': eval_result.total_unfulfilled_penalty,
            'total_cost': (
                eval_result.total_fixed_cost
                + eval_result.total_swap_cost
                + eval_result.total_crash_penalty
                + eval_result.total_unfulfilled_penalty
            ),
            'net_profit': eval_result.net_profit,
            'total_distance_km': solution.total_distance_km(),
            'drone_stats': drone_stats,
            'actions': actions,
            'flight_plan': plan,
            'feasible': eval_result.feasible,
            'income_details': self._build_income_details(eval_result),
            'type_stats': self._build_type_stats(eval_result),
            'route_stats': self._build_route_stats(eval_result),
        }

        return result

    def _build_income_details(self, eval_result):
        details = []
        for oid, delivery_time in eval_result.delivered_orders.items():
            if oid in self.order_manager.orders:
                order = self.order_manager.orders[oid]
                actual_income = order.income_at_delivery(delivery_time)
                details.append({
                    'order_id': oid,
                    'order_type': order.order_type.value,
                    'supply': order.supply_location,
                    'demand': order.demand_location,
                    'weight_kg': order.weight_kg,
                    'full_income': order.full_income,
                    'actual_income': actual_income,
                    'delivery_time_min': delivery_time / 60,
                    'deadline_min': order.deadline_seconds / 60,
                    'late_minutes': max(0, (delivery_time - order.deadline_seconds) / 60),
                })
        return details

    def _build_type_stats(self, eval_result):
        from collections import defaultdict
        stats = defaultdict(lambda: {'count': 0, 'full_income': 0, 'actual_income': 0, 'late_count': 0})
        for oid, delivery_time in eval_result.delivered_orders.items():
            if oid in self.order_manager.orders:
                order = self.order_manager.orders[oid]
                actual = order.income_at_delivery(delivery_time)
                otype = order.order_type.value
                stats[otype]['count'] += 1
                stats[otype]['full_income'] += order.full_income
                stats[otype]['actual_income'] += actual
                if delivery_time > order.deadline_seconds:
                    stats[otype]['late_count'] += 1
        return dict(stats)

    def _build_route_stats(self, eval_result):
        from collections import defaultdict
        stats = defaultdict(lambda: {'count': 0, 'actual_income': 0})
        for oid, delivery_time in eval_result.delivered_orders.items():
            if oid in self.order_manager.orders:
                order = self.order_manager.orders[oid]
                route = f"{order.supply_location}→{order.demand_location}"
                stats[route]['count'] += 1
                stats[route]['actual_income'] += order.income_at_delivery(delivery_time)
        return dict(stats)