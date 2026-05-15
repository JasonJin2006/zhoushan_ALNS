"""
游轮轨迹计算模块
================
游轮沿4段环线循环运行，速度25m/s，总长13.624km。
本模块计算游轮在任意时刻的位置，以及无人机与游轮的汇合点预测。
"""

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .map_data import GeoCoord, horizontal_distance_meters


# ============================================================
# 游轮环线定义
# ============================================================

@dataclass
class CruiseWaypoint:
    """游轮航线航路点"""
    coord: GeoCoord
    index: int  # 航路点序号


class CruiseRoute:
    """
    游轮航线
    ========
    游轮沿4个航路点的环线持续循环运行。
    航路点坐标（海拔15m）：
      0: 122.215359, 30.123374, 15
      1: 122.191617, 30.121327, 15
      2: 122.190785, 30.154022, 15
      3: 122.23828,  30.13972,  15
    回到航路点0形成闭环。

    总长度: 13.624 km
    速度: 25 m/s
    """

    # 游轮航线航路点（5个点，首尾相连）
    WAYPOINTS = [
        GeoCoord(122.215359, 30.123374, 15),
        GeoCoord(122.191617, 30.121327, 15),
        GeoCoord(122.190785, 30.154022, 15),
        GeoCoord(122.23828, 30.13972, 15),
        GeoCoord(122.215359, 30.123374, 15),  # 回到起点
    ]

    TOTAL_LENGTH_KM = 13.624
    SPEED_MPS = 25.0
    SPEED_KMPS = SPEED_MPS / 1000.0  # km/s

    def __init__(self):
        # 计算每段的距离
        self.segments: List[Tuple[GeoCoord, GeoCoord, float]] = []  # (起点, 终点, 距离km)
        self.segment_distances: List[float] = []
        self.cumulative_distances: List[float] = []  # 累计距离
        self._compute_segments()

    def _compute_segments(self):
        """计算各段距离，使用赛题给出的精确总长度进行比例修正"""
        # 先用Haversine公式计算各段原始距离
        raw_distances = []
        for i in range(len(self.WAYPOINTS) - 1):
            p1 = self.WAYPOINTS[i]
            p2 = self.WAYPOINTS[i + 1]
            dist_km = horizontal_distance_meters(p1, p2) / 1000.0
            raw_distances.append(dist_km)

        # 按赛题给出的精确总长度进行比例修正
        raw_total = sum(raw_distances)
        scale = self.TOTAL_LENGTH_KM / raw_total if raw_total > 0 else 1.0

        total = 0.0
        for i in range(len(self.WAYPOINTS) - 1):
            p1 = self.WAYPOINTS[i]
            p2 = self.WAYPOINTS[i + 1]
            dist_km = raw_distances[i] * scale  # 修正后的距离
            self.segments.append((p1, p2, dist_km))
            self.segment_distances.append(dist_km)
            total += dist_km
            self.cumulative_distances.append(total)

    @property
    def total_length_km(self) -> float:
        """环线总长度（公里）"""
        return sum(self.segment_distances)

    @property
    def loop_time_seconds(self) -> float:
        """完成一圈的时间（秒）"""
        return (self.total_length_km * 1000) / self.SPEED_MPS

    @property
    def loop_time_minutes(self) -> float:
        """完成一圈的时间（分钟）"""
        return self.loop_time_seconds / 60.0

    def position_at_time(self, time_seconds: float) -> GeoCoord:
        """
        计算游轮在指定时刻的位置。
        游轮从航路点0出发，持续循环运行。

        参数:
            time_seconds: 仿真时间（秒），0表示仿真开始时刻

        返回:
            游轮在指定时刻的地理坐标（海拔15m）
        """
        if time_seconds < 0:
            time_seconds = 0

        total_km = self.total_length_km
        # 计算游轮已经走过的总距离
        distance_traveled_km = (time_seconds * self.SPEED_KMPS) % total_km

        # 确定在哪一段上
        cumulative = 0.0
        for i, (p1, p2, seg_dist) in enumerate(self.segments):
            if cumulative + seg_dist >= distance_traveled_km:
                # 在这一段上
                progress = (distance_traveled_km - cumulative) / seg_dist if seg_dist > 0 else 0
                lng = p1.lng + (p2.lng - p1.lng) * progress
                lat = p1.lat + (p2.lat - p1.lat) * progress
                return GeoCoord(lng, lat, 15.0)
            cumulative += seg_dist

        # 如果因为浮点误差没有找到，返回起点
        return self.WAYPOINTS[0]

    def position_at_time_minutes(self, time_minutes: float) -> GeoCoord:
        """计算游轮在指定时刻（分钟）的位置"""
        return self.position_at_time(time_minutes * 60)

    def segment_index_at_distance(self, distance_km: float) -> Tuple[int, float]:
        """
        给定沿环线走过的距离，返回所在段索引和段内进度。

        返回:
            (段索引, 段内进度 0~1)
        """
        total_km = self.total_length_km
        d = distance_km % total_km

        cumulative = 0.0
        for i, seg_dist in enumerate(self.segment_distances):
            if cumulative + seg_dist >= d:
                progress = (d - cumulative) / seg_dist if seg_dist > 0 else 0
                return i, progress
            cumulative += seg_dist
        return 0, 0.0


# ============================================================
# 汇合点预测
# ============================================================

class RendezvousPredictor:
    """
    无人机与游轮汇合点预测器
    ========================

    给定无人机当前位置和出发时间，预测无人机与游轮在何时刻何位置汇合。

    策略：
    1. 无人机从某个静态节点出发，需要先飞到游轮航线附近
    2. 无人机先飞到最近的航路点（或指定航路点），到达后沿环线追踪游轮
    3. 预测汇合点时需要考虑：
       - 无人机到游轮航线的飞行时间
       - 游轮在该时间段内的移动距离

    注意：无人机飞往游轮不使用预设航线，而是直接飞向预测的汇合点坐标。
    """

    FLIGHT_ALTITUDE = 100.0  # 无人机飞行高度
    DRONE_SPEED = 25.0       # 无人机水平速度（米/秒）
    DRONE_VERTICAL_SPEED = 5.0  # 无人机垂直速度（米/秒）
    CRUISE_ALT = 15.0        # 游轮海拔高度

    def __init__(self, cruise_route: CruiseRoute):
        self.cruise = cruise_route

    def predict_rendezvous(
        self,
        drone_start: GeoCoord,
        depart_time_seconds: float,
        max_iterations: int = 50,
        tolerance_seconds: float = 0.1
    ) -> Tuple[GeoCoord, float]:
        """
        迭代预测无人机与游轮的汇合点和汇合时间。

        算法：
        1. 猜测汇合时刻 T（初始为 depart_time）
        2. 计算游轮在 T 时刻的位置 C
        3. 计算无人机从起点飞到 C 需要的时间 flight_time
        4. 如果 flight_time ≈ T - depart_time，收敛
        5. 否则更新 T = depart_time + flight_time，继续迭代

        参数:
            drone_start: 无人机起飞位置坐标（地面）
            depart_time_seconds: 无人机起飞时刻（秒）
            max_iterations: 最大迭代次数
            tolerance_seconds: 收敛容差（秒）

        返回:
            (汇合点坐标, 汇合时刻-秒)
        """
        takeoff_time = max(0, self.FLIGHT_ALTITUDE - drone_start.alt) / self.DRONE_VERTICAL_SPEED
        
        T = depart_time_seconds
        
        for _ in range(max_iterations):
            # 1. 计算游轮在时刻 T 的位置
            cruise_pos = self.cruise.position_at_time(T)
            
            # 2. 计算无人机从起点飞到该位置的飞行时间（不含垂直起飞时间）
            target_at_alt = GeoCoord(cruise_pos.lng, cruise_pos.lat, self.FLIGHT_ALTITUDE)
            horizontal_dist_m = horizontal_distance_meters(drone_start, target_at_alt)
            horizontal_time = horizontal_dist_m / self.DRONE_SPEED
            
            # 3. 总飞行时间 = 垂直起飞时间 + 水平飞行时间
            total_flight_time = takeoff_time + horizontal_time
            
            # 4. 检查收敛：如果飞行时间 + 起飞时刻 ≈ 猜测的时刻 T
            expected_T = depart_time_seconds + total_flight_time
            delta_T = abs(expected_T - T)
            
            T = expected_T
            
            if delta_T < tolerance_seconds:
                break
        
        # 最终汇合点
        cruise_pos = self.cruise.position_at_time(T)
        rendezvous = GeoCoord(cruise_pos.lng, cruise_pos.lat, self.CRUISE_ALT)
        
        return rendezvous, T

    def _compute_flight_time(self, drone_start: GeoCoord, target_pos: GeoCoord) -> float:
        """
        计算无人机从起点飞到目标位置的总飞行时间（秒）。
        = 垂直起飞时间 + 水平飞行时间
        （飞往游轮时不计算垂直降落，到达即汇合）
        """
        takeoff_alt_diff = max(0, self.FLIGHT_ALTITUDE - drone_start.alt)
        takeoff_time = takeoff_alt_diff / self.DRONE_VERTICAL_SPEED

        target_at_flight_alt = GeoCoord(target_pos.lng, target_pos.lat, self.FLIGHT_ALTITUDE)
        horizontal_dist_m = horizontal_distance_meters(drone_start, target_at_flight_alt)
        horizontal_time = horizontal_dist_m / self.DRONE_SPEED

        return takeoff_time + horizontal_time

    def compute_rendezvous_flight_distance(
        self,
        drone_start: GeoCoord,
        rendezvous_point: GeoCoord
    ) -> float:
        """
        计算无人机飞往汇合点的总飞行距离（公里）。
        = 垂直起飞距离 + 水平飞行距离
        """
        takeoff_dist_km = max(0, self.FLIGHT_ALTITUDE - drone_start.alt) / 1000.0
        target_at_flight_alt = GeoCoord(rendezvous_point.lng, rendezvous_point.lat, self.FLIGHT_ALTITUDE)
        horizontal_dist_km = horizontal_distance_meters(drone_start, target_at_flight_alt) / 1000.0
        return takeoff_dist_km + horizontal_dist_km

    def compute_rendezvous_from_node(
        self,
        from_node_name: str,
        depart_time_seconds: float,
        map_data=None
    ) -> Tuple[GeoCoord, float, float]:
        """
        从静态节点出发，计算飞往游轮的汇合信息。

        参数:
            from_node_name: 出发节点名称
            depart_time_seconds: 出发时刻（秒）
            map_data: MapData实例（可选，默认使用全局）

        返回:
            (汇合点坐标, 汇合时刻-秒, 总飞行距离-km)
        """
        if map_data is None:
            from .map_data import get_map_data
            map_data = get_map_data()

        airport = map_data.get_airport(from_node_name)
        rendezvous, arrival_time = self.predict_rendezvous(
            airport.coord, depart_time_seconds
        )
        flight_dist = self.compute_rendezvous_flight_distance(airport.coord, rendezvous)
        return rendezvous, arrival_time, flight_dist


# ============================================================
# 全局单例
# ============================================================

_default_cruise: Optional[CruiseRoute] = None
_default_predictor: Optional[RendezvousPredictor] = None

def get_cruise_route() -> CruiseRoute:
    """获取默认游轮航线实例"""
    global _default_cruise
    if _default_cruise is None:
        _default_cruise = CruiseRoute()
    return _default_cruise

def get_rendezvous_predictor() -> RendezvousPredictor:
    """获取默认汇合预测器实例"""
    global _default_predictor
    if _default_predictor is None:
        _default_predictor = RendezvousPredictor(get_cruise_route())
    return _default_predictor
