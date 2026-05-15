"""
破坏算子
========
从当前解中移除一批订单，为修复算子提供优化空间。
"""

import random
import math
from typing import Dict, List, Optional, Set, Tuple
from abc import ABC, abstractmethod

from ..order import Order, OrderManager
from ..map_data import MapData, get_map_data
from .solution import Solution, DronePlan, FlightLeg
from .evaluator import SolutionEvaluator


# ============================================================
# 破坏算子基类
# ============================================================


class DestroyOperator(ABC):
    """破坏算子基类"""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def destroy(
        self,
        solution: Solution,
        q: int,
        order_manager: OrderManager,
        evaluator: SolutionEvaluator,
        **kwargs,
    ) -> Tuple[Solution, Set[int]]:
        """
        从解中移除q个订单。

        返回: (新解, 被移除的订单ID集合)
        注意：不修改原解，返回深拷贝。
        """
        pass


# ============================================================
# Worst Removal - 移除利润贡献最差的订单
# ============================================================


class WorstRemoval(DestroyOperator):
    """
    Worst Removal
    =============
    移除当前解中"净利润贡献最低"的订单。
    贡献 = 订单收入 - 分摊的飞行成本
    """

    def __init__(self):
        super().__init__("worst_removal")

    def destroy(
        self,
        solution: Solution,
        q: int,
        order_manager: OrderManager,
        evaluator: SolutionEvaluator,
        **kwargs,
    ) -> Tuple[Solution, Set[int]]:
        new_sol = solution.deep_copy()
        assigned = list(new_sol.assigned_order_ids())

        if not assigned:
            return new_sol, set()

        # 计算每个订单的利润贡献
        order_contributions = []
        for oid in assigned:
            if oid in order_manager.orders:
                order = order_manager.orders[oid]
                # 查找送达时间
                delivery_time = None
                for plan in new_sol.drone_plans.values():
                    for leg in plan.legs:
                        if oid in leg.unload_orders:
                            delivery_time = leg.arrive_time
                            break

                if delivery_time is not None:
                    income = order.income_at_delivery(delivery_time)
                else:
                    income = 0.0

                # 飞行成本分摊（粗估：每km 0.5元）
                cost_estimate = order.weight_kg * 5.0  # 粗估
                contribution = income - cost_estimate
                order_contributions.append((contribution, oid))

        # 按贡献升序（最差的排前面）
        order_contributions.sort(key=lambda x: x[0])

        # 移除前q个最差的
        actual_q = min(q, len(order_contributions))
        removed = set()
        for i in range(actual_q):
            _, oid = order_contributions[i]
            new_sol.remove_order(oid)
            removed.add(oid)

        # 清理空leg
        new_sol.clean_empty_legs()

        return new_sol, removed


# ============================================================
# Shaw Removal - 移除相似的订单
# ============================================================


class ShawRemoval(DestroyOperator):
    """
    Shaw Removal
    ============
    移除"相似"的订单：同供应地、同需求地、时间段接近。
    释放某一区域，便于重新安排。
    """

    def __init__(self):
        super().__init__("shaw_removal")

    def destroy(
        self,
        solution: Solution,
        q: int,
        order_manager: OrderManager,
        evaluator: SolutionEvaluator,
        **kwargs,
    ) -> Tuple[Solution, Set[int]]:
        new_sol = solution.deep_copy()
        assigned = list(new_sol.assigned_order_ids())

        if not assigned:
            return new_sol, set()

        # 随机选一个种子订单
        seed_id = random.choice(assigned)
        if seed_id not in order_manager.orders:
            return new_sol, set()
        seed = order_manager.orders[seed_id]

        # 计算所有已分配订单与种子的相似度
        similarities = []
        for oid in assigned:
            if oid == seed_id or oid not in order_manager.orders:
                continue
            order = order_manager.orders[oid]
            sim = self._similarity(seed, order)
            similarities.append((sim, oid))

        # 按相似度降序（最相似的排前面）
        similarities.sort(key=lambda x: -x[0])

        # 移除种子 + 最相似的q-1个
        removed = {seed_id}
        new_sol.remove_order(seed_id)

        for i in range(min(q - 1, len(similarities))):
            _, oid = similarities[i]
            new_sol.remove_order(oid)
            removed.add(oid)

        new_sol.clean_empty_legs()
        return new_sol, removed

    def _similarity(self, o1: Order, o2: Order) -> float:
        """计算两个订单的相似度（0-1，1=完全相同）"""
        score = 0.0

        # 同供应地
        if o1.supply_location == o2.supply_location:
            score += 0.35

        # 同需求地
        if o1.demand_location == o2.demand_location:
            score += 0.35

        # 同类型
        if o1.order_type == o2.order_type:
            score += 0.15

        # 时间接近
        time_diff = abs(o1.generation_time_seconds - o2.generation_time_seconds)
        time_sim = max(0, 1.0 - time_diff / 3600)  # 1小时内相似
        score += time_sim * 0.15

        return score


# ============================================================
# Random Removal - 随机移除
# ============================================================


class RandomRemoval(DestroyOperator):
    """
    Random Removal
    ==============
    随机移除q个订单。增加搜索多样性。
    """

    def __init__(self):
        super().__init__("random_removal")

    def destroy(
        self,
        solution: Solution,
        q: int,
        order_manager: OrderManager,
        evaluator: SolutionEvaluator,
        **kwargs,
    ) -> Tuple[Solution, Set[int]]:
        new_sol = solution.deep_copy()
        assigned = list(new_sol.assigned_order_ids())

        if not assigned:
            return new_sol, set()

        actual_q = min(q, len(assigned))
        to_remove = set(random.sample(assigned, actual_q))

        for oid in to_remove:
            new_sol.remove_order(oid)

        new_sol.clean_empty_legs()
        return new_sol, to_remove


# ============================================================
# Route Removal - 移除某架无人机一段行程的所有订单
# ============================================================


class RouteRemoval(DestroyOperator):
    """
    Route Removal
    =============
    随机选一架无人机的一段连续legs，移除其中所有订单。
    整段重构，实现大步搜索。
    """

    def __init__(self):
        super().__init__("route_removal")

    def destroy(
        self,
        solution: Solution,
        q: int,
        order_manager: OrderManager,
        evaluator: SolutionEvaluator,
        **kwargs,
    ) -> Tuple[Solution, Set[int]]:
        new_sol = solution.deep_copy()

        # 随机选一架无人机
        drone_ids = list(new_sol.drone_plans.keys())
        if not drone_ids:
            return new_sol, set()

        drone_id = random.choice(drone_ids)
        plan = new_sol.drone_plans[drone_id]

        if not plan.legs:
            return new_sol, set()

        # 随机选一段连续legs
        n = len(plan.legs)
        start = random.randint(0, max(0, n - 1))
        length = min(random.randint(2, max(2, q // 3)), n - start)
        end = start + length

        # 移除这段legs中的所有订单
        removed = set()
        for i in range(start, end):
            leg = plan.legs[i]
            for oid in leg.load_orders:
                removed.add(oid)
                new_sol.unassigned_order_ids.add(oid)
            leg.load_orders = []
            leg.unload_orders = []

        new_sol.clean_empty_legs()
        return new_sol, removed


# ============================================================
# Whole Leg Removal - 整leg移除
# ============================================================


class WholeLegRemoval(DestroyOperator):
    """
    Whole Leg Removal
    =================
    随机选择一些leg，移除上面绑定的所有订单。
    这会真正创建时间缝隙，让修复算子有机会插入更好的订单。

    策略：优先选择订单数少的leg，以最大化创建的缝隙数量。
    """

    def __init__(self):
        super().__init__("whole_leg_removal")

    def destroy(
        self,
        solution: Solution,
        q: int,
        order_manager: OrderManager,
        evaluator: SolutionEvaluator,
        **kwargs,
    ) -> Tuple[Solution, Set[int]]:
        new_sol = solution.deep_copy()
        assigned = list(new_sol.assigned_order_ids())

        if not assigned:
            return new_sol, set()

        # 收集所有有订单的leg及其订单数量
        leg_info = []  # (order_count, drone_id, leg_index)
        for drone_id, plan in new_sol.drone_plans.items():
            for i, leg in enumerate(plan.legs):
                orders_on_leg = set(leg.load_orders)
                if orders_on_leg:
                    leg_info.append((len(orders_on_leg), drone_id, i, orders_on_leg))

        if not leg_info:
            return new_sol, set()

        # 按订单数排序（最少优先）：最大化创建缝隙的数量
        leg_info.sort(key=lambda x: x[0])

        removed = set()
        actual_q = min(q, len(assigned))

        for count, drone_id, leg_index, orders_on_leg in leg_info:
            if len(removed) >= actual_q:
                break
            for oid in orders_on_leg:
                if oid not in removed:
                    new_sol.remove_order(oid)
                    removed.add(oid)
                    if len(removed) >= actual_q:
                        break

        new_sol.clean_empty_legs()
        return new_sol, removed


# ============================================================
# 工厂函数
# ============================================================


def create_destroy_operators() -> List[DestroyOperator]:
    """创建所有破坏算子"""
    return [
        WorstRemoval(),
        ShawRemoval(),
        RandomRemoval(),
        RouteRemoval(),
        WholeLegRemoval(),
    ]
