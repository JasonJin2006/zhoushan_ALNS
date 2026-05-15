"""
ALNS主引擎
==========
自适应大邻域搜索的核心循环。
"""

import time
import random
import math
from typing import Dict, List, Optional, Set, Tuple

from ..map_data import MapData, get_map_data
from ..order import OrderManager
from .solution import Solution
from .evaluator import SolutionEvaluator
from .construction import GreedyConstructor
from .destroy import DestroyOperator, create_destroy_operators
from .repair import RepairOperator, create_repair_operators
from .config import ALNSConfig


# ============================================================
# 自适应权重管理
# ============================================================


class AdaptiveWeights:
    """
    自适应权重管理
    ==============
    跟踪每个算子的表现，动态调整选择概率。
    """

    def __init__(self, n_destroy: int, n_repair: int, config: ALNSConfig):
        self.config = config
        # 权重
        self.destroy_weights = [1.0] * n_destroy
        self.repair_weights = [1.0] * n_repair
        # 累计得分
        self.destroy_scores = [0.0] * n_destroy
        self.repair_scores = [0.0] * n_repair
        # 使用次数
        self.destroy_counts = [0] * n_destroy
        self.repair_counts = [0] * n_repair

    def select_destroy(self) -> int:
        """轮盘赌选择破坏算子"""
        return self._roulette(self.destroy_weights)

    def select_repair(self) -> int:
        """轮盘赌选择修复算子"""
        return self._roulette(self.repair_weights)

    def update(self, destroy_idx: int, repair_idx: int, score: float):
        """更新算子得分"""
        self.destroy_scores[destroy_idx] += score
        self.repair_scores[repair_idx] += score
        self.destroy_counts[destroy_idx] += 1
        self.repair_counts[repair_idx] += 1

    def update_weights(self):
        """
        每segment轮更新一次权重。
        新权重 = (1-r) × 旧权重 + r × (得分/使用次数)
        """
        r = self.config.reaction_factor

        for i in range(len(self.destroy_weights)):
            if self.destroy_counts[i] > 0:
                avg_score = self.destroy_scores[i] / self.destroy_counts[i]
                self.destroy_weights[i] = (1 - r) * self.destroy_weights[
                    i
                ] + r * avg_score
            self.destroy_scores[i] = 0.0
            self.destroy_counts[i] = 0

        for i in range(len(self.repair_weights)):
            if self.repair_counts[i] > 0:
                avg_score = self.repair_scores[i] / self.repair_counts[i]
                self.repair_weights[i] = (1 - r) * self.repair_weights[
                    i
                ] + r * avg_score
            self.repair_scores[i] = 0.0
            self.repair_counts[i] = 0

    def _roulette(self, weights: List[float]) -> int:
        """轮盘赌选择"""
        total = sum(weights)
        if total <= 0:
            return random.randint(0, len(weights) - 1)
        r = random.random() * total
        cumulative = 0.0
        for i, w in enumerate(weights):
            cumulative += w
            if r <= cumulative:
                return i
        return len(weights) - 1


# ============================================================
# 模拟退火接受准则
# ============================================================


class SimulatedAnnealing:
    """模拟退火接受准则"""

    def __init__(self, config: ALNSConfig):
        self.temperature = config.initial_temperature
        self.cooling_rate = config.cooling_rate
        self.min_temperature = config.min_temperature

    def accept(self, current_profit: float, new_profit: float) -> bool:
        """判断是否接受新解"""
        if new_profit >= current_profit:
            return True
        # 以概率 exp(Δ/T) 接受更差的解
        delta = new_profit - current_profit
        if self.temperature > 0:
            probability = math.exp(delta / self.temperature)
            return random.random() < probability
        return False

    def cool_down(self):
        """降温"""
        self.temperature = max(
            self.min_temperature, self.temperature * self.cooling_rate
        )


# ============================================================
# ALNS引擎
# ============================================================


class ALNSEngine:
    """
    ALNS主引擎
    ==========
    自适应大邻域搜索，用于求解无人机配送调度问题。
    """

    def __init__(
        self,
        order_manager: OrderManager,
        map_data: Optional[MapData] = None,
        config: Optional[ALNSConfig] = None,
    ):
        self.order_manager = order_manager
        self.map_data = map_data or get_map_data()
        self.config = config or ALNSConfig()

        # 评估器
        self.evaluator = SolutionEvaluator(order_manager, self.map_data)

        # 算子
        self.destroy_ops = create_destroy_operators()
        self.repair_ops = create_repair_operators()

        # 自适应权重
        self.adaptive = AdaptiveWeights(
            len(self.destroy_ops), len(self.repair_ops), self.config
        )

        # 模拟退火
        self.sa = SimulatedAnnealing(self.config)

        # 统计
        self.iteration = 0
        self.best_profit = float("-inf")
        self.best_solution = None
        self.best_feasible = False
        self.no_improve_count = 0

    def solve(self) -> Solution:
        """
        运行ALNS，返回最优解。

        流程：
        1. 贪心构造初始解
        2. 迭代：破坏 → 修复 → 评估 → 接受/拒绝
        3. 返回最优解
        """
        start_time = time.time()

        # 1. 构造初始解
        if self.config.verbose:
            print("\n[ALNS] 构造初始解...")
        constructor = GreedyConstructor(self.order_manager, self.map_data, self.config)
        initial_solution = constructor.construct()

        # 评估初始解
        initial_result = self.evaluator.evaluate(initial_solution)
        initial_profit = initial_result.net_profit

        if self.config.verbose:
            print(f"[ALNS] 初始解净利润: {initial_profit:.2f} 元")
            print(f"  配送订单: {len(initial_solution.assigned_order_ids())} 单")
            print(f"  换电次数: {initial_solution.total_swap_count()}")

        # 初始化
        current_solution = initial_solution
        current_profit = initial_profit
        current_feasible = initial_result.feasible
        if not current_feasible:
            current_profit -= 1e9  # 不可行解大量扣分

        self.best_solution = initial_solution.deep_copy()
        self.best_profit = current_profit
        self.best_feasible = current_feasible

        # 2. 迭代
        if self.config.verbose:
            print(f"\n[ALNS] 开始迭代 (max={self.config.max_iterations})...")

        for iteration in range(1, self.config.max_iterations + 1):
            self.iteration = iteration

            # 检查时间限制
            elapsed = time.time() - start_time
            if elapsed > self.config.time_limit_seconds:
                if self.config.verbose:
                    print(
                        f"[ALNS] 时间限制 ({self.config.time_limit_seconds}s) 达到，停止"
                    )
                break

            # 检查无改善限制
            if self.no_improve_count >= self.config.max_no_improve:
                if self.config.verbose:
                    print(f"[ALNS] {self.config.max_no_improve}轮无改善，停止")
                break

            # ---- 选择算子 ----
            destroy_idx = self.adaptive.select_destroy()
            repair_idx = self.adaptive.select_repair()

            destroy_op = self.destroy_ops[destroy_idx]
            repair_op = self.repair_ops[repair_idx]

            # ---- 计算移除数量 ----
            assigned_count = len(current_solution.assigned_order_ids())
            if assigned_count == 0:
                break
            q = min(
                max(
                    self.config.destroy_min,
                    int(assigned_count * self.config.destroy_degree),
                ),
                self.config.destroy_max,
                assigned_count,
            )
            q = max(q, 1)

            # ---- 破坏 ----
            destroyed, removed = destroy_op.destroy(
                current_solution, q, self.order_manager, self.evaluator
            )

            if not removed:
                continue

            # ---- 修复 ----
            repaired = repair_op.repair(
                destroyed,
                removed,
                self.order_manager,
                self.evaluator,
                map_data=self.map_data,
            )

            # ---- 评估 ----
            result = self.evaluator.evaluate(repaired)
            new_profit = result.net_profit
            new_feasible = result.feasible

            # 不可行解扣分
            effective_new_profit = new_profit if new_feasible else new_profit - 1e9
            effective_current_profit = (
                current_profit if current_feasible else current_profit - 1e9
            )

            # ---- 接受准则 ----
            accepted = self.sa.accept(effective_current_profit, effective_new_profit)

            # ---- 更新得分 ----
            if new_profit > self.best_profit and new_feasible:
                score = self.config.score_new_best
            elif new_profit > current_profit:
                score = self.config.score_better
            elif accepted:
                score = self.config.score_accepted
            else:
                score = self.config.score_rejected

            self.adaptive.update(destroy_idx, repair_idx, score)

            # ---- 更新当前解 ----
            if accepted:
                current_solution = repaired
                current_profit = new_profit
                current_feasible = new_feasible

                if new_feasible and new_profit > self.best_profit:
                    self.best_solution = repaired.deep_copy()
                    self.best_profit = new_profit
                    self.best_feasible = True
                    self.no_improve_count = 0
                else:
                    self.no_improve_count += 1
            else:
                self.no_improve_count += 1

            # ---- 降温 ----
            self.sa.cool_down()

            # ---- 更新权重 ----
            if iteration % self.config.segment_size == 0:
                self.adaptive.update_weights()

            # ---- 日志 ----
            if self.config.verbose and iteration % self.config.log_interval == 0:
                elapsed = time.time() - start_time
                print(
                    f"  [{iteration:5d}] "
                    f"当前={current_profit:10.2f} "
                    f"最优={self.best_profit:10.2f} "
                    f"配送={len(current_solution.assigned_order_ids()):4d}单 "
                    f"换电={current_solution.total_swap_count():2d}次 "
                    f"T={self.sa.temperature:6.2f} "
                    f"耗时={elapsed:.1f}s"
                )

        # 3. 最终评估
        if self.best_solution is not None:
            final_result = self.evaluator.evaluate(self.best_solution)
            if self.config.verbose:
                print(f"\n[ALNS] 完成！")
                print(f"  最优净利润: {final_result.net_profit:.2f} 元")
                print(f"  配送订单: {len(self.best_solution.assigned_order_ids())} 单")
                print(f"  总收入: {final_result.total_income:.2f} 元")
                print(f"  固定成本: -{final_result.total_fixed_cost:.2f} 元")
                print(
                    f"  换电成本: -{final_result.total_swap_cost:.2f} 元 ({final_result.total_swap_count}次)"
                )
                print(f"  坠毁惩罚: -{final_result.total_crash_penalty:.2f} 元")
                print(f"  未履约惩罚: -{final_result.total_unfulfilled_penalty:.2f} 元")
                print(f"  总迭代: {self.iteration}")
                print(f"  总耗时: {time.time() - start_time:.1f}s")

        return self.best_solution or initial_solution
