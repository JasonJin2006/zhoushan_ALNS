# 无人机配送调度系统 - Code Wiki

## 1. 项目概述

### 1.1 项目简介

本项目是一个**无人机配送调度系统**，采用自适应大邻域搜索算法（ALNS）为3架无人机生成180分钟的最优飞行计划。系统支持从Excel文件加载订单数据，智能规划取送货路线，并生成详细的飞行计划Excel文件。

### 1.2 技术栈

| 类别 | 技术 |
|------|------|
| 编程语言 | Python 3.x |
| 数据处理 | openpyxl (Excel读写) |
| 优化算法 | ALNS (自适应大邻域搜索) |
| 依赖包 | 仅 openpyxl>=3.1.0 |

### 1.3 项目结构

```
core_src/
├── run_scheduler.py           # 主入口程序
├── requirements.txt           # 依赖清单
├── .gitignore                # Git忽略配置
├── input/                    # 输入数据目录
│   └── *.xlsx               # 订单数据Excel文件
└── drone_engine/             # 核心引擎包
    ├── __init__.py           # 包初始化文档
    ├── map_data.py           # 地图与航线数据
    ├── drone.py              # 无人机飞行计算
    ├── order.py              # 订单处理
    ├── cost.py               # 成本计算
    ├── cruise_ship.py        # 游轮轨迹计算
    ├── flight_plan.py         # 飞行计划生成与验证
    ├── scheduler.py          # ALNS调度器
    └── optimizer/            # ALNS优化器子包
        ├── __init__.py
        ├── config.py          # ALNS参数配置
        ├── solution.py        # 解的表示
        ├── evaluator.py       # 解评估器
        ├── construction.py    # 初始解构造
        ├── destroy.py         # 破坏算子
        ├── repair.py          # 修复算子
        └── alns.py           # ALNS主引擎
```

---

## 2. 整体架构

### 2.1 系统架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        run_scheduler.py                          │
│                    (主入口，调度流程控制)                          │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      DroneScheduler (scheduler.py)                │
│                   (ALNS版调度器，协调优化流程)                      │
└─────────────────────────────────────────────────────────────────┘
                                │
            ┌───────────────────┼───────────────────┐
            ▼                   ▼                   ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   ALNSEngine    │  │ SolutionEvaluator│  │ FlightPlanBuilder│
│   (优化主循环)   │  │   (解评估)       │  │  (计划生成)      │
└─────────────────┘  └─────────────────┘  └─────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Optimizer Package                          │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐             │
│  │ Construction │ │   Destroy    │ │   Repair     │             │
│  │  (初始解构造) │ │  (破坏算子)  │ │  (修复算子)  │             │
│  └──────────────┘ └──────────────┘ └──────────────┘             │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Core Engine Package                         │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐             │
│  │  map_data    │ │    drone     │ │    order     │             │
│  │ (地图航线)    │ │ (飞行计算)   │ │  (订单处理)   │             │
│  └──────────────┘ └──────────────┘ └──────────────┘             │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐             │
│  │  cruise_ship│ │    cost      │ │ flight_plan  │             │
│  │  (游轮轨迹)  │ │  (成本计算)  │ │ (计划生成)   │             │
│  └──────────────┘ └──────────────┘ └──────────────┘             │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流

```
输入数据 (Excel)
      │
      ▼
┌─────────────┐
│ OrderManager │ ──加载订单数据
└─────────────┘
      │
      ▼
┌─────────────┐
│   ALNSEngine │ ──运行ALNS优化
└─────────────┘
      │
      ▼
┌─────────────────────┐
│ SolutionEvaluator   │ ──评估解的可行性
└─────────────────────┘
      │
      ▼
┌─────────────────────┐
│ FlightPlanBuilder   │ ──生成飞行计划
└─────────────────────┘
      │
      ▼
输出文件 (Excel + JSON)
```

---

## 3. 主要模块职责

### 3.1 模块概览

| 模块 | 文件 | 主要职责 |
|------|------|----------|
| **主入口** | run_scheduler.py | 命令行参数解析、调度流程控制、结果导出 |
| **调度器** | scheduler.py | 协调ALNS引擎和评估器，转换解为飞行计划 |
| **地图数据** | map_data.py | 节点、航线、距离矩阵、Dijkstra最短路径 |
| **无人机** | drone.py | 飞行计算、状态管理、载重/电量检查 |
| **订单管理** | order.py | 订单加载、状态跟踪、收入计算 |
| **成本计算** | cost.py | 盈利计算、成本项定义 |
| **游轮轨迹** | cruise_ship.py | 游轮位置预测、无人机-游轮汇合点计算 |
| **飞行计划** | flight_plan.py | 计划生成、验证、导出Excel |

### 3.2 核心引擎包 (drone_engine/)

#### 3.2.1 map_data.py - 地图与航线数据

**核心类：**

- `GeoCoord`: 地理坐标（经度、纬度、海拔高度）
- `Airport`: 机场/起降场节点
- `Route`: 预设航线
- `MapData`: 地图数据容器

**关键常量：**
- 飞行安全高度: 100m
- 无人机水平速度: 25 m/s
- 无人机垂直速度: 5 m/s

**关键方法：**
```python
get_distance(from_node, to_node) -> float  # 获取节点间距离
get_route(from_node, to_node) -> Route      # 获取航线
find_shortest_path(from_node, to_node)     # Dijkstra最短路径
find_nearest_land_airport(node_name)       # 找最近陆地机场
```

**节点定义（7个）：**
- 陆地机场（可换电）: 秀水湾度假村、测试基地L1、先导基地
- 渔船（不可换电）: 渔船1、渔船2、渔船3
- 游轮: 游轮（动态位置）

#### 3.2.2 drone.py - 无人机飞行计算

**核心类：**

- `DroneState`: 无人机实时状态
- `DroneFlightCalculator`: 飞行计算器
- `DroneFleet`: 无人机编队管理

**关键常量：**
```python
DRONE_SPEED_MPS = 25.0           # 水平飞行速度（米/秒）
DRONE_MAX_RANGE_KM = 40.0        # 满电续航里程（公里）
DRONE_BATTERY_DRAIN_RATE = 2.5   # 耗电率（%/km）
DRONE_MAX_PAYLOAD_KG = 6.0       # 最大载重（kg）
DRONE_SWAP_TIME_SECONDS = 180.0   # 换电时间（秒）
DRONE_COUNT = 3                   # 无人机数量
DRONE_FIXED_COST = 289.95       # 每架固定成本（元）
BATTERY_SWAP_COST = 20.0        # 每次换电成本（元）
```

**关键方法：**
```python
fly_between_nodes(from_node, to_node, battery, payload)  # 两节点间飞行
fly_to_cruise(from_node, depart_time, battery, payload) # 飞往游轮
fly_from_cruise(to_node, depart_time, cruise_pos, ...)  # 从游轮起飞
battery_consumption(distance_km) -> float               # 电量消耗计算
```

#### 3.2.3 order.py - 订单处理

**核心类：**

- `OrderType`: 订单类型枚举
- `Order`: 订单数据结构
- `OrderManager`: 订单管理器

**订单类型（5种）：**

| 类型 | 中文名 | 重量(kg) | 时间窗(min) | 迟到惩罚(元/min) |
|------|--------|----------|-------------|------------------|
| SEAFOOD_A | 海鲜A | 1.0 | 40 | 2 |
| SEAFOOD_B | 海鲜B | 1.5 | 30 | 4 |
| SEAFOOD_C | 海鲜C | 2.5 | 15 | 5 |
| DAILY_D | 日用品D | 1.5 | 60 | 4 |
| DAILY_E | 日用品E | 1.0 | 40 | 1 |

**关键方法：**
```python
load_from_excel(filepath, sheet_name) -> int      # 从Excel加载订单
grab_order(order_id) -> bool                      # 抢单
deliver_order(order_id, delivery_time) -> float   # 完成配送
income_at_delivery(delivery_time) -> float        # 计算收入
get_urgent_orders(current_time, limit) -> List   # 获取紧急订单
```

**收入计算规则：**
- 完整收入 = 40元/kg × 重量
- 按时送达: 完整收入
- 迟到: 收入 -= 迟到分钟 × 惩罚率
- 超过截止时间: 收入为0，额外惩罚 -40元/kg

#### 3.2.4 cost.py - 成本计算

**核心类：**

- `CostItem`: 成本项
- `ProfitResult`: 盈利计算结果
- `CostCalculator`: 成本计算器

**成本项：**
```python
DRONE_FIXED_COST = 289.95        # 每架固定成本
BATTERY_SWAP_COST = 20.0         # 每次换电成本
ACCIDENT_PENALTY = -1000.0       # 坠毁惩罚
UNFULFILLED_PENALTY_PER_KG = -40 # 未履约惩罚（元/kg）
```

**目标函数：**
```
净利润 = 总收入 - 固定成本 - 换电成本 - 坠毁惩罚 - 未履约惩罚
```

#### 3.2.5 cruise_ship.py - 游轮轨迹计算

**核心类：**

- `CruiseWaypoint`: 游轮航路点
- `CruiseRoute`: 游轮航线（4个航路点环线）
- `RendezvousPredictor`: 汇合点预测器

**游轮航线：**
- 总长度: 13.624 km
- 速度: 25 m/s
- 一圈耗时: ~9分钟
- 航路点:
  - 0: 122.215359, 30.123374, 15
  - 1: 122.191617, 30.121327, 15
  - 2: 122.190785, 30.154022, 15
  - 3: 122.23828, 30.13972, 15

**关键方法：**
```python
position_at_time(time_seconds) -> GeoCoord          # 计算任意时刻游轮位置
predict_rendezvous(drone_start, depart_time)        # 预测汇合点
compute_rendezvous_from_node(from_node, time)       # 从节点飞往游轮
```

#### 3.2.6 flight_plan.py - 飞行计划生成与验证

**核心类：**

- `ActionType`: 动作类型枚举（起飞/降落）
- `FlightAction`: 单个飞行动作
- `FlightPlan`: 完整飞行计划
- `FlightPlanBuilder`: 计划构建器
- `FlightPlanValidator`: 计划验证器
- `FlightPlanExporter`: 计划导出器

**飞行计划格式（Excel）：**
| 列 | 内容 |
|----|------|
| 时间(分:秒) | 动作时刻 |
| 起降场 | 位置名称 |
| 动作 | 起飞/降落 |
| 参数 | 飞往游轮时填写经纬度高度 |
| 装货订单 | 起飞时装载的订单号 |
| 卸货订单 | 降落时卸载的订单号 |
| 是否换电 | 是/否 |
| 备注 | 额外说明 |

---

## 4. 优化器模块 (optimizer/)

### 4.1 ALNS算法概述

自适应大邻域搜索（ALNS）是一种元启发式算法，通过反复"破坏"和"修复"解来搜索最优解。

```
┌─────────────┐
│  初始解构造   │ ──> GreedyConstructor
└─────────────┘
        │
        ▼
┌─────────────────────────────────────────┐
│           ALNS迭代主循环                 │
│  ┌─────────┐    ┌─────────┐            │
│  │ 破坏算子  │ -> │ 修复算子  │            │
│  │(Destroy) │    │(Repair)  │            │
│  └─────────┘    └─────────┘            │
│        │              │                 │
│        ▼              ▼                 │
│  ┌─────────────────────────┐           │
│  │      SolutionEvaluator   │           │
│  │        (评估解质量)       │           │
│  └─────────────────────────┘           │
│              │                         │
│        是否接受新解？                     │
│        (模拟退火准则)                    │
└─────────────────────────────────────────┘
        │
        ▼
┌─────────────┐
│   最优解     │
└─────────────┘
```

### 4.2 config.py - ALNS参数配置

**ALNSConfig 数据类：**

```python
@dataclass
class ALNSConfig:
    # 迭代控制
    max_iterations: int = 2000          # 最大迭代次数
    max_no_improve: int = 200           # 无改善最大迭代数
    time_limit_seconds: float = 60.0    # 时间限制（秒）

    # 破坏算子
    destroy_min: int = 8                # 最少移除订单数
    destroy_max: int = 50               # 最多移除订单数
    destroy_degree: float = 0.25        # 移除比例

    # 模拟退火
    initial_temperature: float = 30.0    # 初始温度
    cooling_rate: float = 0.995          # 冷却率
    min_temperature: float = 0.5        # 最低温度

    # 初始位置
    initial_positions: Dict[str, str] = {
        "UAV-01": "测试基地L1",
        "UAV-02": "先导基地",
        "UAV-03": "秀水湾度假村",
    }
    max_orders_to_assign: int = 350    # 初始解最多分配订单数

    # 电池管理
    battery_swap_threshold: float = 20.0   # 换电阈值
    battery_safety_reserve: float = 5.0    # 安全余量
```

### 4.3 solution.py - 解的表示

**核心数据结构：**

```python
@dataclass
class FlightLeg:
    """一次飞行段：起飞→降落"""
    from_location: str                    # 起飞地点
    to_location: str                      # 降落地点
    load_orders: List[int]               # 起飞时装载的订单ID
    unload_orders: List[int]              # 降落时卸载的订单ID
    swap_battery: bool = False            # 降落后是否换电
    cruise_target: Optional[GeoCoord]      # 飞往游轮的汇合坐标
    depart_time: float = 0.0             # 起飞时刻
    arrive_time: float = 0.0              # 降落时刻
    flight_distance_km: float = 0.0       # 飞行距离
    battery_before: float = 100.0         # 起飞前电量
    battery_after: float = 100.0         # 降落后电量

@dataclass
class DronePlan:
    """一架无人机的完整飞行计划"""
    drone_id: str
    initial_location: str
    legs: List[FlightLeg]

@dataclass
class Solution:
    """完整解：3架无人机的调度方案"""
    drone_plans: Dict[str, DronePlan]
    unassigned_order_ids: Set[int]
```

### 4.4 evaluator.py - 解评估器

**EvalResult 评估结果：**

```python
@dataclass
class EvalResult:
    feasible: bool                       # 解是否可行
    net_profit: float                   # 净利润
    total_income: float                 # 总收入
    delivered_orders: Dict[int, float]  # order_id → 送达时间
    total_fixed_cost: float             # 固定成本
    total_swap_cost: float              # 换电成本
    total_swap_count: int               # 换电次数
    total_crash_penalty: float          # 坠毁惩罚
    total_unfulfilled_penalty: float    # 未履约惩罚
    violations: List[str]               # 违反的约束
```

**关键方法：**
```python
evaluate(solution) -> EvalResult         # 评估完整解
_check_grab_pool(...)                    # 检查抢单池约束（≤90单）
compute_order_score(order, time) -> float # 计算订单价值评分
```

### 4.5 construction.py - 初始解构造

**GreedyConstructor 类：**

```python
def construct() -> Solution
    # 1. 枚举所有无人机-机场分配方案
    # 2. 对每种方案调用 _schedule_drone
    # 3. 选择净利润最优的方案

def _schedule_drone(state, plan, candidates, assigned, sim_end)
    # 持续调度一架无人机：
    # 1. 检查是否需要返回陆地换电
    # 2. 构建一趟配送 (_build_trip)
    # 3. 重复直到无订单或超时

def _build_trip(state, candidates, assigned, sim_end)
    # 构造一趟配送：
    # Phase 1: 飞到供应地
    # Phase 2: 装载订单
    # Phase 3: 送货路线规划（2-opt优化）
    # Phase 4: 依次送货
    # Phase 5: 电量管理（换电）
```

### 4.6 destroy.py - 破坏算子

**破坏算子接口：**
```python
class DestroyOperator(ABC):
    def destroy(solution, q, order_manager, evaluator) -> (Solution, Set[int])
        # 从解中移除q个订单
```

**实现的破坏算子：**

| 算子 | 类名 | 策略 |
|------|------|------|
| Worst Removal | `WorstRemoval` | 移除利润贡献最低的订单 |
| Shaw Removal | `ShawRemoval` | 移除与随机种子订单相似的订单 |
| Random Removal | `RandomRemoval` | 随机移除订单 |
| Route Removal | `RouteRemoval` | 移除某无人机一段连续legs的所有订单 |
| Whole Leg Removal | `WholeLegRemoval` | 移除订单数少的leg |

### 4.7 repair.py - 修复算子

**修复算子接口：**
```python
class RepairOperator(ABC):
    def repair(solution, removed_orders, order_manager, evaluator) -> Solution
        # 将被移除的订单重新插入解中
```

**实现的修复算子：**

| 算子 | 类名 | 策略 |
|------|------|------|
| Greedy Insertion | `GreedyInsertion` | 按价值降序，插入到利润增量最大的位置 |
| Regret-2 Insertion | `Regret2Insertion` | 优先插入"以后没好位置"的订单 |
| Regret-3 Insertion | `Regret3Insertion` | 同Regret-2但考虑第三好位置 |
| Batch New Trip | `BatchNewTripInsertion` | 批量新建行程，共享飞往供应地成本 |

**关键辅助函数：**
```python
compute_insertion_profit(...)    # 估算插入利润
try_insert_as_new_trip(...)     # 尝试新建行程
_make_flight_leg(...)           # 计算飞行leg
```

### 4.8 alns.py - ALNS主引擎

**核心类：**

```python
class ALNSEngine:
    """ALNS主引擎"""
    
    def solve() -> Solution:
        # 1. 构造初始解 (GreedyConstructor)
        # 2. 迭代：
        #    - 选择破坏算子（轮盘赌）
        #    - 选择修复算子（轮盘赌）
        #    - 计算移除数量 q
        #    - 破坏 → 修复 → 评估
        #    - 模拟退火接受准则
        #    - 更新自适应权重
        # 3. 返回最优解

class AdaptiveWeights:
    """自适应权重管理"""
    def select_destroy() -> int       # 轮盘赌选择破坏算子
    def select_repair() -> int        # 轮盘赌选择修复算子
    def update(destroy_idx, repair_idx, score)  # 更新得分
    def update_weights()              # 更新权重

class SimulatedAnnealing:
    """模拟退火接受准则"""
    def accept(current_profit, new_profit) -> bool
    def cool_down()
```

**自适应权重更新公式：**
```
新权重 = (1 - r) × 旧权重 + r × (得分/使用次数)
```

**接受准则：**
```
if new_profit >= current_profit:
    接受
else:
    以概率 exp(Δ/T) 接受
```

---

## 5. 关键类与函数说明

### 5.1 主入口函数

**run_scheduler.py**

```python
def run(
    data_file: str = None,
    output_dir: str = None,
    iterations: int = 5000,
    time_limit: float = 120.0,
    max_orders: int = 350,
) -> Dict:
    """
    运行调度器
    
    参数:
        data_file: 订单数据Excel文件路径
        output_dir: 输出目录
        iterations: ALNS最大迭代次数
        time_limit: 时间限制（秒）
        max_orders: 初始解最大分配订单数
    
    返回:
        调度结果字典
    """

def export_flight_plan(result: Dict, filepath: str)
    """导出合并的飞行计划到Excel"""

def export_drone_flight_plans(result: Dict, output_dir: str, base_name: str)
    """导出3个独立的无人机飞行计划"""

def export_details(result: Dict, filepath: str, order_manager)
    """导出详细明细到Excel（6个工作表）"""

def export_assigned_orders(result: Dict, order_manager, filepath: str)
    """导出已分配订单列表到文本文件"""
```

### 5.2 调度器核心

**scheduler.py**

```python
class DroneScheduler:
    def __init__(self, map_data, order_manager, config)
    def schedule(initial_positions) -> Dict
        """执行调度，生成完整飞行计划"""
    
    def _solution_to_flight_plan(solution) -> FlightPlan
        """将ALNS解转换为FlightPlan"""
    
    def _build_result(solution, eval_result, plan) -> Dict
        """构建结果字典（兼容run_scheduler.py格式）"""
```

### 5.3 地图与航线

**map_data.py**

```python
class MapData:
    airports: Dict[str, Airport]              # 所有机场
    routes: Dict[str, Route]                  # 所有航线
    distance_matrix: Dict[str, Dict[str, float]]  # 距离矩阵
    
    def get_distance(from_node, to_node) -> float
    def get_route(from_node, to_node) -> Optional[Route]
    def find_shortest_path(from_node, to_node) -> Tuple[float, List[str]]
    def find_nearest_land_airport(node_name) -> Tuple[str, float]
    def total_flight_time(from_node, to_node) -> float
    def total_flight_distance(from_node, to_node) -> float

def get_map_data() -> MapData
    """获取默认地图数据实例（懒加载单例）"""
```

### 5.4 无人机状态

**drone.py**

```python
class DroneState:
    drone_id: str
    battery_percent: float = 100.0
    current_location: str
    status: DroneStatus
    payload_kg: float = 0.0
    loaded_orders: List[int]
    total_distance_km: float = 0.0
    total_swap_count: int = 0
    available_time: float = 0.0

class DroneFlightCalculator:
    def fly_between_nodes(from_node, to_node, battery, payload) -> Dict
    def fly_to_cruise(from_node, depart_time, battery, payload) -> Dict
    def fly_from_cruise(to_node, depart_time, cruise_pos, battery, payload) -> Dict
    @staticmethod
    def battery_consumption(distance_km) -> float
    @staticmethod
    def swap_battery() -> Dict
```

### 5.5 订单管理

**order.py**

```python
class Order:
    order_id: int
    supply_location: str
    demand_location: str
    order_type: OrderType
    weight_kg: float
    time_window_minutes: float
    late_penalty_rate: float
    
    @property
    def full_income() -> float
    @property
    def deadline_seconds() -> float
    @property
    def income_zero_seconds() -> float
    def income_at_delivery(delivery_time_seconds) -> float
    def is_deliverable_at(current_time) -> bool
    def urgency_score(current_time) -> float

class OrderManager:
    orders: Dict[int, Order]
    grabbed_orders: Dict[int, Order]
    delivered_orders: Dict[int, Tuple[Order, float]]
    
    def load_from_excel(filepath, sheet_name) -> int
    def grab_order(order_id) -> bool
    def deliver_order(order_id, delivery_time) -> float
    def get_orders_by_supply(location) -> List[Order]
    def get_urgent_orders(current_time, limit) -> List[Order]
    def get_type_statistics() -> Dict[OrderType, int]
```

---

## 6. 依赖关系

### 6.1 模块依赖图

```
run_scheduler.py
    ├── drone_engine.map_data.get_map_data
    ├── drone_engine.order.OrderManager
    ├── drone_engine.scheduler.DroneScheduler
    ├── drone_engine.optimizer (ALNSConfig, ALNSEngine, ...)
    └── drone_engine.flight_plan (FlightPlanExporter, FlightAction, ActionType)

scheduler.py
    ├── drone_engine.map_data
    ├── drone_engine.order
    ├── drone_engine.cost
    ├── drone_engine.flight_plan
    └── drone_engine.optimizer

optimizer/__init__.py
    ├── optimizer.config (ALNSConfig)
    ├── optimizer.solution (Solution, DronePlan, FlightLeg)
    ├── optimizer.evaluator (SolutionEvaluator, EvalResult)
    ├── optimizer.construction (GreedyConstructor)
    ├── optimizer.destroy (DestroyOperator, create_destroy_operators)
    ├── optimizer.repair (RepairOperator, create_repair_operators)
    └── optimizer.alns (ALNSEngine)

drone_engine/ (基础模块)
    ├── map_data.py (基础数据)
        ├── cruise_ship.py (游轮轨迹)
    ├── drone.py
    ├── order.py
    ├── cost.py
    └── flight_plan.py
```

### 6.2 外部依赖

```
requirements.txt
└── openpyxl >= 3.1.0   # Excel读写
```

---

## 7. 项目运行方式

### 7.1 命令行使用

```bash
# 使用默认路径（input/*.xlsx, output/）
python run_scheduler.py

# 指定输入文件
python run_scheduler.py --input input/订单数据.xlsx

# 简写形式
python run_scheduler.py -i input/订单数据.xlsx -o ./output

# 自定义ALNS参数
python run_scheduler.py -n 5000 -t 120.0 --max-orders 600
```

### 7.2 参数说明

| 参数 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| --input | -i | input/*.xlsx | 订单数据Excel文件 |
| --output | -o | output/ | 输出目录 |
| --iterations | -n | 5000 | ALNS最大迭代次数 |
| --time-limit | -t | 120.0 | 时间限制（秒） |
| --max-orders | - | 600 | 初始解最大分配订单数 |

### 7.3 输出文件

运行后生成以下文件（位于output/目录）：

| 文件 | 内容 |
|------|------|
| `飞行计划_*.xlsx` | 合并的飞行计划（按时间排序） |
| `UAV-01_飞行计划_*.xlsx` | UAV-01独立飞行计划 |
| `UAV-02_飞行计划_*.xlsx` | UAV-02独立飞行计划 |
| `UAV-03_飞行计划_*.xlsx` | UAV-03独立飞行计划 |
| `调度明细_*.xlsx` | 详细明细（6个工作表） |
| `已分配订单_*.txt` | 已分配/未分配订单列表 |
| `调度结果_*.json` | 结果摘要（供前端使用） |

### 7.4 调度明细Excel工作表

| 工作表 | 内容 |
|--------|------|
| 盈利摘要 | 总收入、成本、净利润统计 |
| 各无人机明细 | 每架无人机的配送数、收入、成本 |
| 订单配送明细 | 每个订单的送达时间、收入、迟到情况 |
| 按类型统计 | 各订单类型的配送统计 |
| 按路线统计 | 各路线的配送统计 |
| 订单分配明细 | 每个订单是否已分配、送达时间 |
| UAV-01/02/03日志 | 每架无人机的详细飞行日志 |

### 7.5 编程调用

```python
from drone_engine.map_data import get_map_data
from drone_engine.order import OrderManager
from drone_engine.scheduler import DroneScheduler
from drone_engine.optimizer import ALNSConfig

# 1. 加载订单
order_manager = OrderManager()
order_manager.load_from_excel("input/订单数据.xlsx", "订单任务")

# 2. 配置ALNS参数
config = ALNSConfig(
    max_iterations=5000,
    time_limit_seconds=120.0,
    initial_positions={
        "UAV-01": "测试基地L1",
        "UAV-02": "先导基地",
        "UAV-03": "秀水湾度假村",
    }
)

# 3. 运行调度
scheduler = DroneScheduler(
    map_data=get_map_data(),
    order_manager=order_manager,
    config=config,
)
result = scheduler.schedule()

# 4. 查看结果
print(f"净利润: {result['net_profit']:.2f} 元")
print(f"配送率: {result['delivered_orders'] / result['total_orders'] * 100:.1f}%")
```

---

## 8. 核心算法详解

### 8.1 ALNS迭代流程

```
1. 初始化
   ├─ 构建初始解 (GreedyConstructor)
   └─ 评估初始解

2. 迭代 (直到达到终止条件)
   ├─ 选择破坏算子 (轮盘赌)
   ├─ 选择修复算子 (轮盘赌)
   ├─ 计算移除数量 q
   ├─ 破坏: 从当前解移除q个订单
   ├─ 修复: 将移除的订单重新插入
   ├─ 评估新解
   ├─ 判断是否接受新解 (模拟退火)
   │   └─ 接受: 更新当前解
   │   └─ 拒绝: 保持当前解
   ├─ 更新最优解
   ├─ 更新自适应权重 (每segment轮)
   └─ 降温

3. 终止条件
   ├─ 达到最大迭代次数
   ├─ 超过无改善最大迭代数
   └─ 达到时间限制
```

### 8.2 初始解构造策略

```
GreedyConstructor.construct():
    1. 枚举所有无人机-机场分配方案
    2. 对每种方案：
       ├─ 初始化无人机状态
       └─ 调用 _schedule_drone
    3. 选择净利润最优的方案

_schedule_drone():
    while True:
        ├─ 检查是否需要返回陆地换电
        ├─ 如果电量低 → 返回陆地换电
        ├─ 尝试构造一趟配送
        │   ├─ 选择最优供应地
        │   ├─ 装载订单
        │   ├─ 规划送货路线 (2-opt)
        │   └─ 电量管理
        ├─ 如果有配送 → 更新状态，继续循环
        └─ 如果无订单或超时 → 退出
```

### 8.3 破坏算子策略

| 算子 | 选择逻辑 | 邻域大小 |
|------|----------|----------|
| WorstRemoval | 移除利润贡献最低的订单 | 中等 |
| ShawRemoval | 移除与随机种子相似的订单 | 中等 |
| RandomRemoval | 随机选择 | 中等 |
| RouteRemoval | 移除整段行程 | 大 |
| WholeLegRemoval | 移除订单数少的leg | 中等 |

### 8.4 修复算子策略

| 算子 | 插入逻辑 | 计算复杂度 |
|------|----------|------------|
| GreedyInsertion | 选择利润增量最大的位置 | O(n×m) |
| Regret2Insertion | 选择regret值最大的订单 | O(n²×m) |
| Regret3Insertion | 考虑第三好位置 | O(n²×m) |
| BatchNewTrip | 批量新建行程 | O(n) |

### 8.5 自适应权重机制

```
每轮得分:
├─ 新最优解: +5分
├─ 比当前解好: +3分
├─ 被接受: +1分
└─ 被拒绝: +0分

权重更新 (每segment轮):
新权重 = (1 - r) × 旧权重 + r × (总得分/使用次数)

其中 r = 0.1 (反应因子)
```

---

## 9. 配置参考

### 9.1 赛题参数

```python
# 无人机参数
DRONE_SPEED_MPS = 25.0
DRONE_MAX_PAYLOAD_KG = 6.0
DRONE_BATTERY_DRAIN_RATE = 2.5  # %/km
DRONE_MAX_RANGE_KM = 40.0
DRONE_SWAP_TIME_SECONDS = 180.0
DRONE_FIXED_COST = 289.95
BATTERY_SWAP_COST = 20.0

# 仿真参数
SIMULATION_DURATION_SECONDS = 10800  # 180分钟
```

### 9.2 ALNS推荐配置

| 场景 | max_iterations | time_limit | destroy_degree |
|------|----------------|-------------|----------------|
| 快速测试 | 1000 | 30s | 0.3 |
| 标准运行 | 5000 | 120s | 0.25 |
| 精细优化 | 10000 | 300s | 0.2 |

---

## 10. 扩展指南

### 10.1 添加新的破坏算子

```python
class CustomDestroy(DestroyOperator):
    def __init__(self):
        super().__init__("custom_destroy")
    
    def destroy(self, solution, q, order_manager, evaluator, **kwargs):
        new_sol = solution.deep_copy()
        # 实现破坏逻辑
        return new_sol, removed_orders
```

### 10.2 添加新的修复算子

```python
class CustomRepair(RepairOperator):
    def __init__(self):
        super().__init__("custom_repair")
    
    def repair(self, solution, removed_orders, order_manager, evaluator, **kwargs):
        # 实现修复逻辑
        return repaired_solution
```

### 10.3 自定义输出格式

修改 `flight_plan.py` 中的 `FlightPlanExporter.export_to_excel()` 方法，或在 `run_scheduler.py` 中添加新的导出函数。

---

## 11. 常见问题

### Q1: 如何提高配送率？
- 增加 max_orders 参数
- 降低 time_limit_seconds（延长优化时间）
- 调整初始位置配置

### Q2: 如何减少换电次数？
- 调整 battery_swap_threshold 参数
- 优化初始位置使无人机分布更均匀

### Q3: 如何处理超时订单？
- ALNS会自动评估迟到惩罚
- 可以调整 ALNSConfig 中的 cooling_rate 控制搜索强度

### Q4: 如何添加新的订单类型？
在 `order.py` 的 `OrderType` 枚举中添加新类型，并更新 `ORDER_TYPE_PROPERTIES` 映射。

---

## 12. 参考资料

- **ALNS算法**: P. Shaw. "Using Constraint Programming and Local Search Methods to Solve Vehicle and Crew Scheduling Problems." CP, 1998.
- **赛题数据**: 无人机配送仿真竞赛数据集
- **地图数据来源**: 赛题提供的Excel数据文件

---

*文档生成时间: 2026-05-15*
*项目版本: 1.0*
