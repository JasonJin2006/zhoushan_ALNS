"""
成本计算模块
============
包括无人机固定成本、换电成本、事故惩罚等。
目标函数：最大化盈利 = 总收入 - 总成本 - 总惩罚
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .drone import (
    DRONE_FIXED_COST, BATTERY_SWAP_COST,
    ACCIDENT_PENALTY, OVERWEIGHT_PENALTY_PER_KG, UNFULFILLED_PENALTY_PER_KG,
    DRONE_COUNT,
)
from .order import OrderManager, Order, INCOME_PER_KG


# ============================================================
# 成本项定义
# ============================================================

@dataclass
class CostItem:
    """成本项"""
    name: str           # 成本项名称
    amount: float       # 金额（正数=成本/惩罚，负数=收入）
    category: str       # 类别: 'fixed', 'swap', 'income', 'penalty'


@dataclass
class ProfitResult:
    """盈利计算结果"""
    total_income: float = 0.0           # 总收入（已送达订单）
    total_fixed_cost: float = 0.0       # 无人机固定成本
    total_swap_cost: float = 0.0        # 换电成本
    total_overweight_penalty: float = 0.0  # 超重惩罚
    total_accident_penalty: float = 0.0    # 事故惩罚
    total_unfulfilled_penalty: float = 0.0  # 未履约惩罚

    @property
    def total_cost(self) -> float:
        """总成本（固定+换电+所有惩罚）"""
        return (self.total_fixed_cost + self.total_swap_cost +
                self.total_overweight_penalty + self.total_accident_penalty +
                self.total_unfulfilled_penalty)

    @property
    def net_profit(self) -> float:
        """净利润 = 总收入 - 总成本"""
        return self.total_income - self.total_cost

    def summary(self) -> str:
        """生成盈利摘要"""
        lines = [
            f"=== 盈利计算结果 ===",
            f"总收入（已送达）: {self.total_income:.2f} 元",
            f"无人机固定成本:   -{self.total_fixed_cost:.2f} 元",
            f"换电成本:         -{self.total_swap_cost:.2f} 元",
            f"超重惩罚:         -{self.total_overweight_penalty:.2f} 元",
            f"事故惩罚:         -{self.total_accident_penalty:.2f} 元",
            f"未履约惩罚:       -{self.total_unfulfilled_penalty:.2f} 元",
            f"---",
            f"净利润: {self.net_profit:.2f} 元",
        ]
        return "\n".join(lines)


# ============================================================
# 成本计算器
# ============================================================

class CostCalculator:
    """
    成本计算器
    ==========
    综合计算所有成本和收入，得出最终盈利。
    """

    def __init__(self, order_manager: OrderManager, drone_swap_count: int = 0):
        self.order_manager = order_manager
        self.drone_swap_count = drone_swap_count

    def calculate(
        self,
        overweight_kg: float = 0.0,
        accident_count: int = 0,
        include_all_drones_fixed_cost: bool = True
    ) -> ProfitResult:
        """
        计算最终盈利。

        参数:
            overweight_kg: 总超重量（kg）
            accident_count: 事故次数
            include_all_drones_fixed_cost: 是否包含所有无人机固定成本（无论是否使用）

        返回:
            ProfitResult 盈利结果
        """
        result = ProfitResult()

        # 1. 总收入
        result.total_income = self.order_manager.get_delivered_revenue()

        # 2. 固定成本（所有无人机，无论是否使用）
        if include_all_drones_fixed_cost:
            result.total_fixed_cost = DRONE_FIXED_COST * DRONE_COUNT
        else:
            result.total_fixed_cost = DRONE_FIXED_COST  # 只算一架

        # 3. 换电成本
        result.total_swap_cost = self.drone_swap_count * BATTERY_SWAP_COST

        # 4. 超重惩罚
        if overweight_kg > 0:
            result.total_overweight_penalty = abs(OVERWEIGHT_PENALTY_PER_KG) * overweight_kg

        # 5. 事故惩罚
        result.total_accident_penalty = abs(ACCIDENT_PENALTY) * accident_count

        # 6. 未履约惩罚
        result.total_unfulfilled_penalty = abs(self.order_manager.get_unfulfilled_penalty())

        return result

    @staticmethod
    def calculate_order_income(
        order: Order,
        delivery_time_seconds: float
    ) -> Dict[str, float]:
        """
        计算单个订单的收入明细。

        返回:
            字典包含:
            - full_income: 完整收入
            - actual_income: 实际收入
            - late_minutes: 迟到分钟数
            - late_penalty: 迟到惩罚金额
            - is_unfulfilled: 是否未完成
        """
        full_income = order.full_income
        actual_income = order.income_at_delivery(delivery_time_seconds)

        late_minutes = 0.0
        late_penalty = 0.0
        is_unfulfilled = False

        if delivery_time_seconds > order.deadline_seconds:
            late_minutes = (delivery_time_seconds - order.deadline_seconds) / 60.0
            late_penalty = late_minutes * order.late_penalty_rate

            if actual_income <= 0:
                is_unfulfilled = True
                actual_income = 0.0

        return {
            'full_income': full_income,
            'actual_income': actual_income,
            'late_minutes': late_minutes,
            'late_penalty': late_penalty,
            'is_unfulfilled': is_unfulfilled,
            'unfulfilled_penalty': order.penalty_if_unfulfilled() if is_unfulfilled else 0.0,
        }

    @staticmethod
    def marginal_profit_per_km(order: Order) -> float:
        """
        计算订单的边际利润率（元/公里）。
        用于评估一个订单是否值得飞。

        粗略估计：收入 / 预估飞行距离
        """
        # 预估单程飞行距离（取平均约5km）
        avg_distance = 5.0
        # 来回距离
        round_trip = avg_distance * 2
        # 单程耗电成本（按固定成本分摊）
        return order.full_income / round_trip

    @staticmethod
    def order_value_density(order: Order) -> float:
        """
        计算订单的价值密度（元/kg）。
        用于评估载荷利用效率。
        """
        return order.full_income / order.weight_kg

    @staticmethod
    def time_pressure_score(order: Order, current_time: float) -> float:
        """
        计算订单的时间压力得分。
        得分越高表示越紧迫，应该优先配送。

        考虑因素：
        1. 剩余时间比例
        2. 惩罚率
        3. 价值
        """
        remaining = max(0, order.deadline_seconds - current_time)
        total_window = order.time_window_minutes * 60

        if total_window == 0:
            return float('inf')

        time_ratio = remaining / total_window  # 1.0=刚生成, 0.0=截止

        # 时间越紧、惩罚率越高、价值越大 → 得分越高
        urgency = (1 - time_ratio) * order.late_penalty_rate * order.weight_kg
        return urgency
