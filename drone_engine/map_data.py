"""
地图与航线数据模块
==================
定义7个节点(3个陆地机场 + 3个渔船 + 1个游轮)、30条预设航线、距离矩阵。
所有坐标来自赛局数据文件。
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ============================================================
# 坐标与距离计算
# ============================================================


@dataclass(frozen=True)
class GeoCoord:
    """地理坐标（经度、纬度、海拔高度-米）"""

    lng: float  # 经度
    lat: float  # 纬度
    alt: float  # 海拔高度（米）

    def __str__(self):
        return f"{self.lng:.6f},{self.lat:.6f},{int(self.alt)}"


def haversine_distance(p1: GeoCoord, p2: GeoCoord) -> float:
    """
    使用Vincenty公式计算两个地理坐标之间的水平距离（公里）。
    相比Haversine公式，Vincenty公式对于中等距离更准确。
    """
    a = 6378.137  # 地球长半轴（km）
    f = 1 / 298.257223563  # 地球扁率
    b = a * (1 - f)  # 短半轴

    lat1 = math.radians(p1.lat)
    lat2 = math.radians(p2.lat)
    lon1 = math.radians(p1.lng)
    lon2 = math.radians(p2.lng)

    L = lon2 - lon1
    U1 = math.atan((1 - f) * math.tan(lat1))
    U2 = math.atan((1 - f) * math.tan(lat2))

    sinU1 = math.sin(U1)
    cosU1 = math.cos(U1)
    sinU2 = math.sin(U2)
    cosU2 = math.cos(U2)

    lam = L
    lam_prev = 0.0
    iter_limit = 100

    for _ in range(iter_limit):
        sin_lam = math.sin(lam)
        cos_lam = math.cos(lam)
        sin_sigma = math.sqrt(
            (cosU2 * sin_lam) ** 2 + (cosU1 * sinU2 - sinU1 * cosU2 * cos_lam) ** 2
        )
        if abs(sin_sigma) < 1e-12:
            return 0.0
        cos_sigma = sinU1 * sinU2 + cosU1 * cosU2 * cos_lam
        sigma = math.atan2(sin_sigma, cos_sigma)

        sin_alpha = cosU1 * cosU2 * sin_lam / sin_sigma
        cos_sq_alpha = 1 - sin_alpha ** 2
        if abs(cos_sq_alpha) < 1e-12:
            cos_2sigma_m = 0.0
        else:
            cos_2sigma_m = cos_sigma - 2 * sinU1 * sinU2 / cos_sq_alpha

        C = f / 16 * cos_sq_alpha * (4 + f * (4 - 3 * cos_sq_alpha))
        lam_prev = lam
        lam = L + (1 - C) * f * sin_alpha * (
            sigma + C * sin_sigma * (cos_2sigma_m + C * cos_sigma * (-1 + 2 * cos_2sigma_m ** 2))
        )

        if abs(lam - lam_prev) < 1e-12:
            break

    u_sq = cos_sq_alpha * (a ** 2 - b ** 2) / (b ** 2)
    A = 1 + u_sq / 16384 * (4096 + u_sq * (-768 + u_sq * (320 - 175 * u_sq)))
    B = u_sq / 1024 * (256 + u_sq * (-128 + u_sq * (74 - 47 * u_sq)))
    delta_sigma = B * sin_sigma * (
        cos_2sigma_m + B / 4 * (cos_sigma * (-1 + 2 * cos_2sigma_m ** 2) - B / 6 * cos_2sigma_m * (-3 + 4 * sin_sigma ** 2) * (-3 + 4 * cos_2sigma_m ** 2))
    )

    return b * A * (sigma - delta_sigma)


def euclidean_distance_3d(p1: GeoCoord, p2: GeoCoord) -> float:
    """
    近似3D欧几里得距离（米）。
    先将经纬度差转换为米，再结合海拔差计算。
    纬度1度 ≈ 111km, 经度1度 ≈ 111km × cos(纬度)
    """
    lat_mid = math.radians((p1.lat + p2.lat) / 2)
    dx = (p2.lng - p1.lng) * 111000 * math.cos(lat_mid)  # 东西方向（米）
    dy = (p2.lat - p1.lat) * 111000  # 南北方向（米）
    dz = p2.alt - p1.alt  # 高度差（米）
    return math.sqrt(dx**2 + dy**2 + dz**2)


def horizontal_distance_meters(p1: GeoCoord, p2: GeoCoord) -> float:
    """
    计算两个坐标之间的水平距离（米）。
    使用 Haversine 公式计算球面距离（准确的地球表面距离）。
    用于游轮相关动态航线距离计算。
    """
    return haversine_distance(p1, p2) * 1000.0


# ============================================================
# 节点定义
# ============================================================


@dataclass
class Airport:
    """机场/起降场节点"""

    name: str
    node_type: str  # 'land' 陆地机场, 'boat' 渔船, 'cruise' 游轮
    coord: GeoCoord
    can_swap_battery: bool  # 是否可换电

    @property
    def is_land(self) -> bool:
        return self.node_type == "land"

    @property
    def is_boat(self) -> bool:
        return self.node_type == "boat"

    @property
    def is_cruise(self) -> bool:
        return self.node_type == "cruise"


# ============================================================
# 航线定义
# ============================================================


@dataclass
class Route:
    """预设航线"""

    name: str
    from_node: str  # 出发节点名称
    to_node: str  # 到达节点名称
    from_coord: GeoCoord  # 出发坐标（飞行高度100m）
    to_coord: GeoCoord  # 到达坐标（飞行高度100m）
    distance_km: float  # 航线距离（公里）
    speed_mps: float = 25.0  # 飞行速度（米/秒）
    safety_altitude: float = 100.0  # 安全高度（米）

    @property
    def flight_time_seconds(self) -> float:
        """纯水平飞行时间（秒）"""
        return (self.distance_km * 1000) / self.speed_mps

    @property
    def flight_time_minutes(self) -> float:
        """纯水平飞行时间（分钟）"""
        return self.flight_time_seconds / 60.0


@dataclass
class RouteKey:
    """航线查询键（支持双向查询）"""

    from_node: str
    to_node: str

    def __hash__(self):
        return hash((self.from_node, self.to_node))

    def __eq__(self, other):
        if not isinstance(other, RouteKey):
            return False
        return self.from_node == other.from_node and self.to_node == other.to_node


# ============================================================
# 地图数据（完整定义）
# ============================================================


class MapData:
    """
    地图数据容器
    包含所有节点、航线、距离矩阵等静态数据。
    """

    # 飞行安全高度（米）
    FLIGHT_ALTITUDE = 100.0

    # 无人机水平飞行速度（米/秒）
    DRONE_SPEED = 25.0

    # 无人机垂直飞行速度（米/秒）
    DRONE_VERTICAL_SPEED = 5.0

    def __init__(self):
        self.airports: Dict[str, Airport] = {}
        self.routes: Dict[str, Route] = {}  # key: "from->to"
        self.distance_matrix: Dict[str, Dict[str, float]] = {}  # km
        self._init_airports()
        self._init_routes()
        self._init_distance_matrix()

    def _init_airports(self):
        """初始化7个节点"""
        airport_defs = [
            # 陆地机场（可换电）
            Airport("秀水湾度假村", "land", GeoCoord(122.180875, 30.169504, 46), True),
            Airport("测试基地L1", "land", GeoCoord(122.240364, 30.106533, 15), True),
            Airport("先导基地", "land", GeoCoord(122.203299, 30.102015, 17), True),
            # 渔船（不可换电）
            Airport("渔船1", "boat", GeoCoord(122.2, 30.125467, 10), False),
            Airport("渔船2", "boat", GeoCoord(122.218428, 30.129785, 10), False),
            Airport("渔船3", "boat", GeoCoord(122.204762, 30.144235, 10), False),
        ]
        for ap in airport_defs:
            self.airports[ap.name] = ap

    def _init_routes(self):
        """初始化30条预设航线（来自赛题数据）"""
        # 航线数据：[名称, 出发地, 到达地, 距离km]
        route_defs = [
            ("测试基地-先导", "测试基地L1", "先导基地", 3.607),
            ("测试基地-秀水", "测试基地L1", "秀水湾度假村", 9.033),
            ("测试基地-渔船1", "测试基地L1", "渔船1", 4.420),
            ("测试基地-渔船2", "测试基地L1", "渔船2", 3.334),
            ("测试基地-渔船3", "测试基地L1", "渔船3", 5.407),
            ("秀水-先导", "秀水湾度假村", "先导基地", 7.787),
            ("秀水-渔船1", "秀水湾度假村", "渔船1", 5.218),
            ("秀水-渔船2", "秀水湾度假村", "渔船2", 5.699),
            ("秀水-渔船3", "秀水湾度假村", "渔船3", 3.625),
            ("先导基地-渔船1", "先导基地", "渔船1", 2.619),
            ("先导基地-渔船2", "先导基地", "渔船2", 3.406),
            ("先导基地-渔船3", "先导基地", "渔船3", 4.683),
            ("渔船1-渔船2", "渔船1", "渔船2", 1.839),
            ("渔船1-渔船3", "渔船1", "渔船3", 2.131),
            ("渔船2-渔船3", "渔船2", "渔船3", 2.074),
            # 反向航线（距离相同）
            ("先导-测试基地", "先导基地", "测试基地L1", 3.607),
            ("秀水-测试基地", "秀水湾度假村", "测试基地L1", 9.033),
            ("渔船1-测试基地", "渔船1", "测试基地L1", 4.420),
            ("渔船2-测试基地", "渔船2", "测试基地L1", 3.334),
            ("渔船3-测试基地", "渔船3", "测试基地L1", 5.407),
            ("先导-秀水", "先导基地", "秀水湾度假村", 7.787),
            ("渔船1-秀水", "渔船1", "秀水湾度假村", 5.218),
            ("渔船2-秀水", "渔船2", "秀水湾度假村", 5.699),
            ("渔船3-秀水", "渔船3", "秀水湾度假村", 3.625),
            ("渔船1-先导基地", "渔船1", "先导基地", 2.619),
            ("渔船2-先导基地", "渔船2", "先导基地", 3.406),
            ("渔船3-先导基地", "渔船3", "先导基地", 4.683),
            ("渔船2-渔船1", "渔船2", "渔船1", 1.839),
            ("渔船3-渔船1", "渔船3", "渔船1", 2.131),
            ("渔船3-渔船2", "渔船3", "渔船2", 2.074),
        ]

        for name, from_node, to_node, dist in route_defs:
            from_ap = self.airports[from_node]
            to_ap = self.airports[to_node]
            # 飞行时使用安全高度100m的坐标
            from_coord = GeoCoord(
                from_ap.coord.lng, from_ap.coord.lat, self.FLIGHT_ALTITUDE
            )
            to_coord = GeoCoord(to_ap.coord.lng, to_ap.coord.lat, self.FLIGHT_ALTITUDE)
            route = Route(name, from_node, to_node, from_coord, to_coord, dist)
            key = f"{from_node}->{to_node}"
            self.routes[key] = route

    def _init_distance_matrix(self):
        """从航线数据构建距离矩阵"""
        for key, route in self.routes.items():
            from_node = route.from_node
            to_node = route.to_node
            if from_node not in self.distance_matrix:
                self.distance_matrix[from_node] = {}
            self.distance_matrix[from_node][to_node] = route.distance_km

    # ============================================================
    # 查询接口
    # ============================================================

    def get_airport(self, name: str) -> Airport:
        """获取机场信息"""
        return self.airports[name]

    def get_land_airports(self) -> List[Airport]:
        """获取所有陆地机场（可换电）"""
        return [ap for ap in self.airports.values() if ap.is_land]

    def get_boat_airports(self) -> List[Airport]:
        """获取所有渔船节点"""
        return [ap for ap in self.airports.values() if ap.is_boat]

    def get_route(self, from_node: str, to_node: str) -> Optional[Route]:
        """获取指定航线"""
        key = f"{from_node}->{to_node}"
        return self.routes.get(key)

    def get_distance(self, from_node: str, to_node: str) -> Optional[float]:
        """获取两个节点之间的距离（公里）"""
        if from_node in self.distance_matrix:
            return self.distance_matrix[from_node].get(to_node)
        return None

    def get_all_static_nodes(self) -> List[str]:
        """获取所有静态节点名称（不含游轮）"""
        return list(self.airports.keys())

    def vertical_takeoff_distance(self, node_name: str) -> float:
        """
        计算从指定节点垂直起飞到飞行高度的距离（公里）。
        包括：从地面海拔到飞行安全高度(100m)的垂直距离。
        """
        ap = self.airports[node_name]
        alt_diff = self.FLIGHT_ALTITUDE - ap.coord.alt
        return max(0, alt_diff) / 1000.0  # 转换为公里

    def vertical_landing_distance(self, node_name: str) -> float:
        """
        计算从飞行高度垂直降落到指定节点的距离（公里）。
        """
        return self.vertical_takeoff_distance(node_name)  # 距离相同

    def vertical_takeoff_time(self, node_name: str) -> float:
        """垂直起飞时间（秒）"""
        ap = self.airports[node_name]
        alt_diff = max(0, self.FLIGHT_ALTITUDE - ap.coord.alt)
        return alt_diff / self.DRONE_VERTICAL_SPEED

    def vertical_landing_time(self, node_name: str) -> float:
        """垂直降落时间（秒）"""
        return self.vertical_takeoff_time(node_name)

    def total_flight_distance(self, from_node: str, to_node: str) -> float:
        """
        计算从 from_node 起飞到 to_node 降落的总飞行距离（公里）。
        = 垂直起飞距离 + 水平航线距离 + 垂直降落距离
        """
        takeoff_dist = self.vertical_takeoff_distance(from_node)
        route_dist = self.get_distance(from_node, to_node)
        if route_dist is None:
            raise ValueError(f"没有从 {from_node} 到 {to_node} 的预设航线")
        landing_dist = self.vertical_landing_distance(to_node)
        return takeoff_dist + route_dist + landing_dist

    def total_flight_time(self, from_node: str, to_node: str) -> float:
        """
        计算从 from_node 起飞到 to_node 降落的总飞行时间（秒）。
        = 垂直起飞时间 + 水平飞行时间 + 垂直降落时间
        注意：垂直起降和水平飞行不能同时进行。
        """
        takeoff_time = self.vertical_takeoff_time(from_node)
        route = self.get_route(from_node, to_node)
        if route is None:
            raise ValueError(f"没有从 {from_node} 到 {to_node} 的预设航线")
        horizontal_time = route.flight_time_seconds
        landing_time = self.vertical_landing_time(to_node)
        return takeoff_time + horizontal_time + landing_time

    def find_shortest_path(
        self, from_node: str, to_node: str
    ) -> Tuple[float, List[str]]:
        """
        使用Dijkstra算法查找两个静态节点之间的最短路径。
        返回: (总距离km, 路径节点列表)
        """
        if from_node == to_node:
            return 0.0, [from_node]

        nodes = self.get_all_static_nodes()
        dist = {n: float("inf") for n in nodes}
        prev = {n: None for n in nodes}
        dist[from_node] = 0.0
        visited = set()

        while len(visited) < len(nodes):
            # 找未访问节点中距离最小的
            u = None
            min_dist = float("inf")
            for n in nodes:
                if n not in visited and dist[n] < min_dist:
                    min_dist = dist[n]
                    u = n
            if u is None or dist[u] == float("inf"):
                break
            visited.add(u)

            for v in nodes:
                if v not in visited:
                    d = self.get_distance(u, v)
                    if d is not None and dist[u] + d < dist[v]:
                        dist[v] = dist[u] + d
                        prev[v] = u

        if dist[to_node] == float("inf"):
            raise ValueError(f"无法从 {from_node} 到达 {to_node}")

        # 重建路径
        path = []
        current = to_node
        while current is not None:
            path.append(current)
            current = prev[current]
        path.reverse()

        return dist[to_node], path

    def find_nearest_land_airport(self, node_name: str) -> Tuple[str, float]:
        """
        查找距离指定节点最近的陆地机场。
        返回: (机场名称, 距离km)
        """
        best_name = None
        best_dist = float("inf")
        for ap in self.get_land_airports():
            dist = self.get_distance(node_name, ap.name)
            if dist is not None and dist < best_dist:
                best_dist = dist
                best_name = ap.name
        if best_name is None:
            raise ValueError(f"无法从 {node_name} 到达任何陆地机场")
        return best_name, best_dist


# ============================================================
# 全局单例
# ============================================================

# 默认地图数据实例
_default_map: Optional[MapData] = None


def get_map_data() -> MapData:
    """获取默认地图数据实例（懒加载单例）"""
    global _default_map
    if _default_map is None:
        _default_map = MapData()
    return _default_map
