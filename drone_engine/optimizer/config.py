"""
ALNS优化器配置
==============
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ALNSConfig:
    """ALNS算法参数"""

    # ---- 迭代控制 ----
    max_iterations: int = 2000          # 最大迭代次数（减少以加快速度）
    max_no_improve: int = 200            # 无改善最大迭代数（提前停止）
    time_limit_seconds: float = 60.0      # 时间限制（秒）

    # ---- 破坏算子 ----
    destroy_min: int = 8                 # 最少移除订单数
    destroy_max: int = 50                # 最多移除订单数
    destroy_degree: float = 0.25         # 移除比例（增大以加快搜索）

    # ---- 模拟退火 ----
    initial_temperature: float = 30.0    # 初始温度（降低）
    cooling_rate: float = 0.995          # 冷却率（加快冷却）
    min_temperature: float = 0.5         # 最低温度

    # ---- 自适应权重 ----
    segment_size: int = 50              # 每segment轮更新一次权重
    reaction_factor: float = 0.1        # 权重更新反应因子 r
    score_new_best: float = 5.0         # 找到新最优解的得分
    score_better: float = 3.0           # 找到更好解的得分
    score_accepted: float = 1.0         # 被接受的解的得分
    score_rejected: float = 0.0         # 被拒绝的解的得分

    # ---- 初始解构造 ----
    initial_positions: Dict[str, str] = field(default_factory=lambda: {
        "UAV-01": "测试基地L1",
        "UAV-02": "先导基地",
        "UAV-03": "秀水湾度假村",
    })
    max_orders_to_assign: int = 350     # 初始解最多分配多少订单

    # ---- 电池管理 ----
    battery_swap_threshold: float = 20.0   # 换电阈值（%），低于此值考虑换电
    battery_safety_reserve: float = 5.0    # 安全余量（%），防止坠毁
    last_trip_reserve: float = 0.0         # 最后一趟的余量（仿真结束不返航）

    # ---- 修复算子 ----
    insertion_candidates: int = 10      # 每次插入考虑的候选位置数
    max_payload_kg: float = 6.0         # 最大载重

    # ---- 调试 ----
    verbose: bool = True                # 是否打印进度
    log_interval: int = 100             # 日志间隔（轮）
