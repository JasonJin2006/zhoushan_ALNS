"""
无人机飞行计算模块
==================
3架无人机，最大6kg载重，25m/s速度，2.5%/km耗电，40km续航，3分钟换电。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .map_data import MapData, GeoCoord, get_map_data
from .cruise_ship import CruiseRoute, RendezvousPredictor, get_cruise_route, get_rendezvous_predictor


# ============================================================
# 常量定义
# ============================================================

# 无人机性能参数
DRONE_SPEED_MPS = 25.0           # 水平飞行速度（米/秒）
DRONE_VERTICAL_SPEED_MPS = 5.0   # 垂直速度（米/秒）
DRONE_MAX_RANGE_KM = 40.0        # 满电续航里程（公里）
DRONE_BATTERY_DRAIN_RATE = 2.5   # 耗电率（%/km）
DRONE_MAX_PAYLOAD_KG = 6.0       # 最大载重（kg）
DRONE_SWAP_TIME_MINUTES = 3.0    # 换电时间（分钟）
DRONE_SWAP_TIME_SECONDS = 180.0  # 换电时间（秒）
DRONE_FLIGHT_ALTITUDE = 100.0    # 飞行安全高度（米）
DRONE_COUNT = 3                  # 可用无人机数量

# 成本参数
DRONE_FIXED_COST = 289.95        # 每架无人机固定成本（元）
BATTERY_SWAP_COST = 20.0         # 每次换电成本（元）

# 惩罚参数
ACCIDENT_PENALTY = -1000.0       # 耗尽电量事故惩罚（元）
OVERWEIGHT_PENALTY_PER_KG = -60.0  # 超重惩罚（元/kg）
UNFULFILLED_PENALTY_PER_KG = -40.0  # 未履约惩罚（元/kg）

# 仿真时间参数
SIMULATION_DURATION_MINUTES = 180.0  # 仿真总时长（分钟，真实时间/仿真时间）
SIMULATION_DURATION_SECONDS = 10800.0  # 仿真总时长（秒）


# ============================================================
# 无人机状态
# ============================================================

class DroneStatus(Enum):
    """无人机状态"""
    IDLE = "idle"               # 空闲（在地面上）
    FLYING = "flying"           # 飞行中
    SWAPPING = "swapping"       # 换电中
    LOADING = "loading"         # 装货中（瞬时完成）
    UNLOADING = "unloading"     # 卸货中（瞬时完成）
    ON_CRUISE = "on_cruise"     # 在游轮上（随游轮移动）
    CRASHED = "crashed"         # 坠毁（电量耗尽）


@dataclass
class DroneState:
    """无人机实时状态"""
    drone_id: str               # 无人机编号
    battery_percent: float = 100.0  # 当前电量百分比
    current_location: str = ""  # 当前位置（节点名称）
    current_coord: Optional[GeoCoord] = None  # 当前精确坐标
    status: DroneStatus = DroneStatus.IDLE
    payload_kg: float = 0.0     # 当前载重（kg）
    loaded_orders: List[int] = field(default_factory=list)  # 已装载的订单编号
    total_distance_km: float = 0.0  # 累计飞行距离
    total_swap_count: int = 0   # 累计换电次数
    available_time: float = 0.0  # 下一次可用时刻（秒）

    @property
    def remaining_range_km(self) -> float:
        """当前电量可飞行的剩余里程（公里）"""
        return self.battery_percent / DRONE_BATTERY_DRAIN_RATE

    @property
    def can_fly(self) -> bool:
        """是否可以飞行（未坠毁且有电量）"""
        return self.status != DroneStatus.CRASHED and self.battery_percent > 0


# ============================================================
# 飞行计算
# ============================================================

class DroneFlightCalculator:
    """
    无人机飞行计算器
    ================
    提供各种飞行场景的电量、时间、距离计算。
    """

    def __init__(self, map_data: Optional[MapData] = None):
        self.map_data = map_data or get_map_data()
        self.cruise = get_cruise_route()
        self.predictor = get_rendezvous_predictor()

    # ---- 基础计算 ----

    @staticmethod
    def battery_consumption(distance_km: float) -> float:
        """
        计算飞行指定距离的电量消耗（百分比）。
        耗电率: 2.5%/km（均匀耗电）
        包括垂直起降和水平飞行的所有距离。
        """
        return distance_km * DRONE_BATTERY_DRAIN_RATE

    @staticmethod
    def distance_from_battery(battery_percent: float) -> float:
        """
        根据剩余电量计算可飞行距离（公里）。
        """
        return battery_percent / DRONE_BATTERY_DRAIN_RATE

    @staticmethod
    def flight_time_seconds(distance_km: float) -> float:
        """
        计算水平飞行指定距离的时间（秒）。
        速度: 25 m/s
        """
        return (distance_km * 1000) / DRONE_SPEED_MPS

    @staticmethod
    def flight_time_minutes(distance_km: float) -> float:
        """计算水平飞行指定距离的时间（分钟）"""
        return DroneFlightCalculator.flight_time_seconds(distance_km) / 60.0

    # ---- 两节点间飞行 ----

    def fly_between_nodes(
        self,
        from_node: str,
        to_node: str,
        current_battery: float,
        current_payload: float = 0.0
    ) -> Dict:
        """
        计算从 from_node 飞到 to_node 的完整飞行信息。

        返回字典包含:
            - total_distance_km: 总飞行距离（公里）
            - horizontal_distance_km: 水平飞行距离（公里）
            - takeoff_distance_km: 垂直起飞距离（公里）
            - landing_distance_km: 垂直降落距离（公里）
            - total_time_seconds: 总飞行时间（秒）
            - battery_consumed: 电量消耗（%）
            - battery_remaining: 飞行后剩余电量（%）
            - feasible: 是否可行（电量是否足够）
            - overweight: 是否超重
        """
        # 距离计算
        takeoff_dist = self.map_data.vertical_takeoff_distance(from_node)
        landing_dist = self.map_data.vertical_landing_distance(to_node)
        route_dist = self.map_data.get_distance(from_node, to_node)
        if route_dist is None:
            raise ValueError(f"没有从 {from_node} 到 {to_node} 的预设航线")

        total_dist = takeoff_dist + route_dist + landing_dist

        # 时间计算
        takeoff_time = self.map_data.vertical_takeoff_time(from_node)
        landing_time = self.map_data.vertical_landing_time(to_node)
        route = self.map_data.get_route(from_node, to_node)
        horizontal_time = route.flight_time_seconds
        total_time = takeoff_time + horizontal_time + landing_time

        # 电量计算
        battery_consumed = self.battery_consumption(total_dist)
        battery_remaining = current_battery - battery_consumed

        # 可行性检查
        feasible = battery_remaining >= 0
        overweight = current_payload > DRONE_MAX_PAYLOAD_KG

        return {
            'total_distance_km': total_dist,
            'horizontal_distance_km': route_dist,
            'takeoff_distance_km': takeoff_dist,
            'landing_distance_km': landing_dist,
            'total_time_seconds': total_time,
            'takeoff_time_seconds': takeoff_time,
            'horizontal_time_seconds': horizontal_time,
            'landing_time_seconds': landing_time,
            'battery_consumed': battery_consumed,
            'battery_remaining': battery_remaining,
            'feasible': feasible,
            'overweight': overweight,
        }

    # ---- 飞往游轮 ----

    def fly_to_cruise(
        self,
        from_node: str,
        depart_time_seconds: float,
        current_battery: float,
        current_payload: float = 0.0
    ) -> Dict:
        """
        计算从静态节点飞往游轮的飞行信息。

        包括：
        - 预测汇合点
        - 飞行距离和时间
        - 电量消耗
        - 飞到游轮后还需要在游轮上等待/随游轮移动的阶段

        注意：飞往游轮时，无人机会到达游轮上方，然后垂直降落。
        此处计算飞到汇合点的信息（不包括从游轮起飞的部分）。
        """
        airport = self.map_data.get_airport(from_node)

        # 预测汇合点
        rendezvous, arrival_time, flight_dist = self.predictor.compute_rendezvous_from_node(
            from_node, depart_time_seconds, self.map_data
        )

        # 从游轮上空垂直降落到游轮甲板
        landing_alt_diff = max(0, DRONE_FLIGHT_ALTITUDE - self.cruise.WAYPOINTS[0].alt)
        landing_dist_km = landing_alt_diff / 1000.0
        landing_time_seconds = landing_alt_diff / DRONE_VERTICAL_SPEED_MPS

        # 总距离
        total_dist = flight_dist + landing_dist_km
        total_time = (arrival_time - depart_time_seconds) + landing_time_seconds

        # 电量计算
        battery_consumed = self.battery_consumption(total_dist)
        battery_remaining = current_battery - battery_consumed

        feasible = battery_remaining >= 0
        overweight = current_payload > DRONE_MAX_PAYLOAD_KG

        return {
            'rendezvous_point': rendezvous,
            'arrival_time_seconds': arrival_time,
            'landing_time_seconds': landing_time_seconds,
            'total_time_seconds': total_time,
            'flight_distance_km': flight_dist,
            'landing_distance_km': landing_dist_km,
            'total_distance_km': total_dist,
            'battery_consumed': battery_consumed,
            'battery_remaining': battery_remaining,
            'feasible': feasible,
            'overweight': overweight,
        }

    # ---- 从游轮起飞 ----

    def fly_from_cruise(
        self,
        to_node: str,
        depart_time_seconds: float,
        cruise_position: GeoCoord,
        current_battery: float,
        current_payload: float = 0.0
    ) -> Dict:
        """
        计算从游轮飞往静态节点的飞行信息。

        参数:
            to_node: 目标节点名称
            depart_time_seconds: 起飞时刻（秒）
            cruise_position: 游轮在起飞时刻的位置坐标
            current_battery: 当前电量百分比
            current_payload: 当前载重（kg）
        """
        to_airport = self.map_data.get_airport(to_node)

        # 垂直起飞（从游轮甲板到飞行高度）
        takeoff_alt_diff = max(0, DRONE_FLIGHT_ALTITUDE - self.cruise.WAYPOINTS[0].alt)
        takeoff_dist_km = takeoff_alt_diff / 1000.0
        takeoff_time_seconds = takeoff_alt_diff / DRONE_VERTICAL_SPEED_MPS

        # 水平飞行到目标节点（飞行高度）
        target_at_flight_alt = GeoCoord(to_airport.coord.lng, to_airport.coord.lat, DRONE_FLIGHT_ALTITUDE)
        cruise_at_flight_alt = GeoCoord(cruise_position.lng, cruise_position.lat, DRONE_FLIGHT_ALTITUDE)
        from .map_data import horizontal_distance_meters
        horizontal_dist_m = horizontal_distance_meters(cruise_at_flight_alt, target_at_flight_alt)
        horizontal_dist_km = horizontal_dist_m / 1000.0
        horizontal_time_seconds = horizontal_dist_m / DRONE_SPEED_MPS

        # 垂直降落
        landing_dist_km = self.map_data.vertical_landing_distance(to_node)
        landing_time_seconds = self.map_data.vertical_landing_time(to_node)

        # 总距离和时间
        total_dist = takeoff_dist_km + horizontal_dist_km + landing_dist_km
        total_time = takeoff_time_seconds + horizontal_time_seconds + landing_time_seconds

        # 电量计算
        battery_consumed = self.battery_consumption(total_dist)
        battery_remaining = current_battery - battery_consumed

        feasible = battery_remaining >= 0
        overweight = current_payload > DRONE_MAX_PAYLOAD_KG

        return {
            'total_distance_km': total_dist,
            'takeoff_distance_km': takeoff_dist_km,
            'horizontal_distance_km': horizontal_dist_km,
            'landing_distance_km': landing_dist_km,
            'total_time_seconds': total_time,
            'takeoff_time_seconds': takeoff_time_seconds,
            'horizontal_time_seconds': horizontal_time_seconds,
            'landing_time_seconds': landing_time_seconds,
            'battery_consumed': battery_consumed,
            'battery_remaining': battery_remaining,
            'feasible': feasible,
            'overweight': overweight,
            'arrival_time_seconds': depart_time_seconds + total_time,
        }

    # ---- 换电 ----

    @staticmethod
    def swap_battery() -> Dict:
        """
        换电操作信息。

        换电条件：
        - 只能在陆地机场进行
        - 换电时间3分钟
        - 换电后电量恢复到100%
        - 换电不消耗飞行距离（不计入电量消耗）
        """
        return {
            'swap_time_seconds': DRONE_SWAP_TIME_SECONDS,
            'swap_time_minutes': DRONE_SWAP_TIME_MINUTES,
            'battery_after_swap': 100.0,
            'swap_cost': BATTERY_SWAP_COST,
        }

    # ---- 悬停等待 ----

    @staticmethod
    def hover_consumption(wait_time_seconds: float) -> float:
        """
        计算悬停等待的电量消耗。

        假设：悬停耗电率与水平飞行相同（25m/s对应的耗电率）。
        即：悬停1秒相当于飞行25米的耗电。

        参数:
            wait_time_seconds: 等待时间（秒）

        返回:
            电量消耗（百分比）
        """
        # 等效飞行距离 = 速度 × 时间
        equiv_distance_km = (DRONE_SPEED_MPS * wait_time_seconds) / 1000.0
        return equiv_distance_km * DRONE_BATTERY_DRAIN_RATE

    # ---- 载重检查 ----

    @staticmethod
    def check_payload(current_weight: float, additional_weight: float) -> Dict:
        """
        检查是否可以装载更多货物。

        返回:
            - total_weight: 总重量
            - can_load: 是否可以装载
            - overweight_kg: 超重量（kg），0表示不超重
        """
        total = current_weight + additional_weight
        can_load = total <= DRONE_MAX_PAYLOAD_KG
        overweight_kg = max(0, total - DRONE_MAX_PAYLOAD_KG)
        return {
            'total_weight': total,
            'can_load': can_load,
            'overweight_kg': overweight_kg,
        }

    # ---- 事故判定 ----

    @staticmethod
    def check_battery_crash(battery_after_flight: float) -> bool:
        """
        检查飞行后是否会因电量耗尽而坠毁。
        """
        return battery_after_flight < 0


# ============================================================
# 无人机调度器（状态管理）
# ============================================================

class DroneFleet:
    """
    无人机编队管理
    ==============
    管理3架无人机的状态、位置、电量、载重等。
    """

    def __init__(self, map_data: Optional[MapData] = None):
        self.map_data = map_data or get_map_data()
        self.calculator = DroneFlightCalculator(self.map_data)
        self.drones: Dict[str, DroneState] = {}

        # 初始化3架无人机
        for i in range(1, DRONE_COUNT + 1):
            drone_id = f"UAV-{i:02d}"
            self.drones[drone_id] = DroneState(drone_id=drone_id)

    def reset(self, initial_positions: Optional[Dict[str, str]] = None):
        """
        重置所有无人机到初始状态。

        参数:
            initial_positions: 可选的初始位置映射 {drone_id: node_name}
                              如果不指定，所有无人机默认在第一个陆地机场
        """
        land_airports = self.map_data.get_land_airports()
        default_pos = land_airports[0].name if land_airports else "测试基地L1"

        for drone_id in self.drones:
            pos = initial_positions.get(drone_id, default_pos) if initial_positions else default_pos
            airport = self.map_data.get_airport(pos)
            self.drones[drone_id] = DroneState(
                drone_id=drone_id,
                current_location=pos,
                current_coord=airport.coord,
            )

    def get_drone(self, drone_id: str) -> DroneState:
        """获取无人机状态"""
        return self.drones[drone_id]

    def get_available_drones(self, at_time: float) -> List[DroneState]:
        """获取在指定时刻可用的无人机列表"""
        return [
            d for d in self.drones.values()
            if d.can_fly and d.available_time <= at_time
        ]

    def get_idle_drone_at(self, node_name: str, at_time: float) -> Optional[DroneState]:
        """获取在指定节点和时刻空闲的无人机"""
        for d in self.drones.values():
            if (d.can_fly and d.available_time <= at_time
                    and d.current_location == node_name
                    and d.status in (DroneStatus.IDLE, DroneStatus.ON_CRUISE)):
                return d
        return None

    def total_fixed_cost(self) -> float:
        """计算所有无人机的固定成本（无论是否使用）"""
        return DRONE_FIXED_COST * DRONE_COUNT

    def total_swap_count(self) -> int:
        """计算总换电次数"""
        return sum(d.total_swap_count for d in self.drones.values())

    def total_swap_cost(self) -> float:
        """计算总换电成本"""
        return self.total_swap_count() * BATTERY_SWAP_COST
