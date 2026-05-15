"""
解的表示
========
ALNS操作的核心数据结构。
一架无人机的计划 = 一系列FlightLeg（起飞→降落对）。
"""

import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from ..map_data import GeoCoord


# ============================================================
# 飞行段
# ============================================================

@dataclass
class FlightLeg:
    """
    一次飞行段：起飞→降落
    
    设计原则：
    - 每次飞行leg要么装载订单（从供应地取货），要么卸载订单（送货到需求地），不能同时做
    - 纯飞行leg（无装卸货）仅用于移动无人机位置或换电
    
    语义澄清：
    - load_orders: 降落时卸载的订单（这些订单在飞行途中被送达）
    - unload_orders: 保留用于兼容，但实际业务中主要用于卸载
    - 注意：实际上，一个leg对应一次"取货→送货"或单独"送货"
    
    对应输出格式的两行：
      起飞行：时间, from_location, 起飞, cruise_target, load_orders(本趟装载的订单), "", 否, ""
      降落行：时间, to_location, 降落, "", "", unload_orders(本趟卸载的订单), 换电?, ""
    """
    from_location: str                       # 起飞地点
    to_location: str                         # 降落地点
    load_orders: List[int] = field(default_factory=list)    # 起飞时装载的订单ID（从供应地取货）
    unload_orders: List[int] = field(default_factory=list)  # 降落时卸载的订单ID（送达需求地）
    swap_battery: bool = False               # 降落后是否换电
    cruise_target: Optional[GeoCoord] = None # 飞往游轮时的汇合坐标（参数列）
    
    depart_time: float = 0.0                 # 起飞时刻（秒）
    arrive_time: float = 0.0                 # 降落时刻（秒）
    flight_distance_km: float = 0.0          # 飞行距离（公里）
    battery_before: float = 100.0            # 起飞前电量
    battery_after: float = 100.0             # 降落后电量

    @property
    def is_pickup_leg(self) -> bool:
        """是否为取货leg（起飞时装载订单）"""
        return len(self.load_orders) > 0

    @property
    def is_delivery_leg(self) -> bool:
        """是否为送货leg（降落时卸载订单）"""
        return len(self.unload_orders) > 0

    @property
    def is_flying(self) -> bool:
        """是否为飞行leg（非原地）"""
        return self.from_location != self.to_location or self.flight_distance_km > 0

    @property
    def is_pure_swap(self) -> bool:
        """是否为纯换电leg（无装卸货）"""
        return self.swap_battery and not self.load_orders and not self.unload_orders

    def assigned_order_ids(self) -> Set[int]:
        """该飞行段负责的所有订单（取货的订单）"""
        return set(self.load_orders)

    def delivered_order_ids(self) -> Set[int]:
        """该飞行段送达的所有订单"""
        return set(self.unload_orders)

    def has_orders(self) -> bool:
        """是否包含任何订单"""
        return len(self.load_orders) > 0 or len(self.unload_orders) > 0

    def deep_copy(self) -> 'FlightLeg':
        return FlightLeg(
            from_location=self.from_location,
            to_location=self.to_location,
            load_orders=list(self.load_orders),
            unload_orders=list(self.unload_orders),
            swap_battery=self.swap_battery,
            cruise_target=self.cruise_target,
            depart_time=self.depart_time,
            arrive_time=self.arrive_time,
            flight_distance_km=self.flight_distance_km,
            battery_before=self.battery_before,
            battery_after=self.battery_after,
        )


# ============================================================
# 无人机计划
# ============================================================

@dataclass
class DronePlan:
    """一架无人机的完整飞行计划"""
    drone_id: str
    initial_location: str                   # 初始位置（陆地机场）
    legs: List[FlightLeg] = field(default_factory=list)

    def assigned_order_ids(self) -> Set[int]:
        """该无人机负责的所有订单"""
        orders = set()
        for leg in self.legs:
            orders.update(leg.load_orders)
        return orders

    @property
    def last_location(self) -> str:
        """最后降落位置"""
        if self.legs:
            return self.legs[-1].to_location
        return self.initial_location

    @property
    def last_time(self) -> float:
        """最后可用时刻"""
        if self.legs:
            last = self.legs[-1]
            t = last.arrive_time
            if last.swap_battery:
                t += 180.0  # 换电3分钟
            return t
        return 0.0

    @property
    def total_swap_count(self) -> int:
        return sum(1 for leg in self.legs if leg.swap_battery)

    @property
    def total_distance_km(self) -> float:
        return sum(leg.flight_distance_km for leg in self.legs)

    def remove_order(self, order_id: int):
        """从计划中移除一个订单"""
        for leg in self.legs:
            if order_id in leg.load_orders:
                leg.load_orders.remove(order_id)
            if order_id in leg.unload_orders:
                leg.unload_orders.remove(order_id)

    def deep_copy(self) -> 'DronePlan':
        return DronePlan(
            drone_id=self.drone_id,
            initial_location=self.initial_location,
            legs=[leg.deep_copy() for leg in self.legs],
        )


# ============================================================
# 完整解
# ============================================================

@dataclass
class Solution:
    """
    ALNS解：3架无人机的完整调度方案

    核心概念：
    - assigned_order_ids: 在计划中的订单（会被抢并配送）
    - unassigned_order_ids: 不在计划中的订单（不抢，0成本）
    """
    drone_plans: Dict[str, DronePlan] = field(default_factory=dict)
    unassigned_order_ids: Set[int] = field(default_factory=set)

    def assigned_order_ids(self) -> Set[int]:
        """所有已分配的订单"""
        orders = set()
        for plan in self.drone_plans.values():
            orders.update(plan.assigned_order_ids())
        return orders

    def total_swap_count(self) -> int:
        return sum(p.total_swap_count for p in self.drone_plans.values())

    def total_distance_km(self) -> float:
        return sum(p.total_distance_km for p in self.drone_plans.values())

    def find_order(self, order_id: int) -> Optional[Tuple[str, int]]:
        """
        查找订单在哪个无人机的哪个leg中。
        返回 (drone_id, leg_index) 或 None。
        """
        for drone_id, plan in self.drone_plans.items():
            for i, leg in enumerate(plan.legs):
                if order_id in leg.load_orders:
                    return (drone_id, i)
        return None

    def remove_order(self, order_id: int):
        """从解中移除一个订单（从所有无人机的计划中删除）"""
        for plan in self.drone_plans.values():
            plan.remove_order(order_id)
        self.unassigned_order_ids.add(order_id)

    def deep_copy(self) -> 'Solution':
        return Solution(
            drone_plans={did: p.deep_copy() for did, p in self.drone_plans.items()},
            unassigned_order_ids=set(self.unassigned_order_ids),
        )

    def clean_empty_legs(self):
        """移除没有订单的空飞行段（保留换电段）"""
        for plan in self.drone_plans.values():
            cleaned = []
            for leg in plan.legs:
                # 纯换电leg保留（只有swap_battery为True）
                if leg.swap_battery and not leg.load_orders and not leg.unload_orders:
                    cleaned.append(leg)
                    continue
                # 有任何订单的leg保留（load或unload）
                if leg.has_orders():
                    cleaned.append(leg)
                    continue
                # 其他空leg全部跳过（不需要保留用于状态转移）
                # 这样可以避免生成无效的空载飞行
            plan.legs = cleaned
