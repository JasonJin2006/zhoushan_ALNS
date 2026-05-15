"""
飞行计划生成与验证模块
======================
生成符合赛题格式的飞行计划，并验证计划的合法性。
飞行计划格式：时间(分:秒) | 起降场 | 动作 | 参数 | 装货订单 | 卸货订单 | 是否换电 | 备注
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

from .map_data import MapData, GeoCoord, get_map_data
from .cruise_ship import CruiseRoute, get_cruise_route
from .drone import (
    DroneFlightCalculator, DroneFleet, DroneState, DroneStatus,
    DRONE_SPEED_MPS, DRONE_MAX_PAYLOAD_KG, DRONE_BATTERY_DRAIN_RATE,
    DRONE_SWAP_TIME_SECONDS, DRONE_FLIGHT_ALTITUDE,
)
from .order import Order, OrderManager


# ============================================================
# 飞行计划数据结构
# ============================================================

class ActionType(Enum):
    """飞行动作类型"""
    TAKEOFF = "起飞"
    LAND = "降落"


@dataclass
class FlightAction:
    """单个飞行动作"""
    time_str: str          # 时间字符串 "分:秒" 如 "0:0", "10:30"
    time_seconds: float    # 时间（秒）
    location: str          # 起降场名称
    action: ActionType     # 动作类型
    parameters: str        # 参数（经度,纬度,高度）- 飞往游轮时填写
    load_orders: str       # 装货订单号（逗号分隔的数字）
    unload_orders: str     # 卸货订单号（逗号分隔的数字）
    swap_battery: str      # 是否换电 "是"/"否"
    remark: str            # 备注

    def to_row(self) -> List[str]:
        """转换为Excel行格式（所有值为字符串）"""
        return [
            self.time_str,
            self.location,
            self.action.value,
            self.parameters,
            self.load_orders,
            self.unload_orders,
            self.swap_battery,
            self.remark,
        ]


@dataclass
class FlightPlan:
    """完整飞行计划"""
    actions: List[FlightAction] = field(default_factory=list)

    def add_action(self, action: FlightAction):
        """添加一个飞行动作"""
        self.actions.append(action)

    def to_rows(self) -> List[List[str]]:
        """转换为Excel行格式"""
        return [a.to_row() for a in self.actions]

    def sort_by_time(self):
        """按时间排序"""
        self.actions.sort(key=lambda a: a.time_seconds)


# ============================================================
# 飞行计划构建器
# ============================================================

class FlightPlanBuilder:
    """
    飞行计划构建器
    ==============
    辅助构建合法的飞行计划。

    使用流程：
    1. 创建构建器，指定无人机编队和订单管理器
    2. 按时间顺序添加动作（起飞、降落、换电等）
    3. 构建器自动计算电量消耗、载重变化等
    4. 生成最终飞行计划
    """

    def __init__(
        self,
        map_data: Optional[MapData] = None,
        order_manager: Optional[OrderManager] = None,
    ):
        self.map_data = map_data or get_map_data()
        self.order_manager = order_manager or OrderManager()
        self.calculator = DroneFlightCalculator(self.map_data)
        self.cruise = get_cruise_route()
        self.plans: Dict[str, FlightPlan] = {}  # drone_id -> plan
        self.drone_states: Dict[str, DroneState] = {}  # drone_id -> state

    def init_drone(self, drone_id: str, initial_position: str):
        """初始化无人机状态"""
        airport = self.map_data.get_airport(initial_position)
        self.drone_states[drone_id] = DroneState(
            drone_id=drone_id,
            current_location=initial_position,
            current_coord=airport.coord,
        )
        self.plans[drone_id] = FlightPlan()

    def add_takeoff(
        self,
        drone_id: str,
        time_seconds: float,
        location: str,
        load_order_ids: Optional[List[int]] = None,
        target_cruise_coord: Optional[GeoCoord] = None,
    ) -> FlightAction:
        """
        添加起飞动作。

        参数:
            drone_id: 无人机编号
            time_seconds: 起飞时刻（秒）
            location: 起飞地点名称
            load_order_ids: 要装载的订单编号列表
            target_cruise_coord: 如飞往游轮，填写预计汇合点坐标
        """
        state = self.drone_states[drone_id]

        # 计算装载订单的总重量
        load_str = ""
        if load_order_ids:
            load_str = ",".join(str(oid) for oid in load_order_ids)
            for oid in load_order_ids:
                if oid in self.order_manager.orders:
                    order = self.order_manager.orders[oid]
                    state.loaded_orders.append(oid)
                    state.payload_kg += order.weight_kg

        # 参数列
        parameters = ""
        if target_cruise_coord:
            parameters = str(target_cruise_coord)

        time_str = self._seconds_to_time_str(time_seconds)

        action = FlightAction(
            time_str=time_str,
            time_seconds=time_seconds,
            location=location,
            action=ActionType.TAKEOFF,
            parameters=parameters,
            load_orders=load_str,
            unload_orders="",
            swap_battery="否",
            remark="",
        )

        self.plans[drone_id].add_action(action)
        state.status = DroneStatus.FLYING
        state.available_time = time_seconds  # 将在降落时更新

        return action

    def add_landing(
        self,
        drone_id: str,
        time_seconds: float,
        location: str,
        unload_order_ids: Optional[List[int]] = None,
        swap_battery: bool = False,
    ) -> FlightAction:
        """
        添加降落动作。

        参数:
            drone_id: 无人机编号
            time_seconds: 降落时刻（秒）
            location: 降落地点名称
            unload_order_ids: 要卸载的订单编号列表
            swap_battery: 是否换电
        """
        state = self.drone_states[drone_id]

        # 计算卸载订单
        unload_str = ""
        if unload_order_ids:
            unload_str = ",".join(str(oid) for oid in unload_order_ids)
            for oid in unload_order_ids:
                if oid in self.order_manager.orders:
                    order = self.order_manager.orders[oid]
                    state.payload_kg -= order.weight_kg
                    if oid in state.loaded_orders:
                        state.loaded_orders.remove(oid)

        # 换电
        swap_str = "是" if swap_battery else "否"
        if swap_battery:
            state.battery_percent = 100.0
            state.total_swap_count += 1

        time_str = self._seconds_to_time_str(time_seconds)

        # 更新状态
        state.current_location = location
        state.available_time = time_seconds
        if location == "游轮":
            state.status = DroneStatus.ON_CRUISE
        else:
            state.status = DroneStatus.IDLE

        action = FlightAction(
            time_str=time_str,
            time_seconds=time_seconds,
            location=location,
            action=ActionType.LAND,
            parameters="",
            load_orders="",
            unload_orders=unload_str,
            swap_battery=swap_str,
            remark="",
        )

        self.plans[drone_id].add_action(action)
        return action

    def get_plan(self, drone_id: str) -> FlightPlan:
        """获取指定无人机的飞行计划"""
        return self.plans.get(drone_id, FlightPlan())

    def get_combined_plan(self) -> FlightPlan:
        """
        获取合并的飞行计划（所有无人机的动作按时间排序）。
        这是最终需要导入仿真平台的格式。
        """
        combined = FlightPlan()
        for plan in self.plans.values():
            combined.actions.extend(plan.actions)
        combined.sort_by_time()
        return combined

    @staticmethod
    def _seconds_to_time_str(seconds: float) -> str:
        """
        将秒数转换为时间字符串 "分:秒"。
        例如: 0 → "0:0", 30 → "0:30", 90 → "1:30", 630 → "10:30"
        注意：赛题格式不补零，如 "0:0" 而非 "0:00"
        """
        total_seconds = round(seconds)
        minutes = total_seconds // 60
        secs = total_seconds % 60
        return f"{minutes}:{secs}"


# ============================================================
# 飞行计划验证器
# ============================================================

@dataclass
class ValidationResult:
    """验证结果"""
    valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def add_error(self, msg: str):
        self.errors.append(msg)
        self.valid = False

    def add_warning(self, msg: str):
        self.warnings.append(msg)

    def summary(self) -> str:
        lines = ["=== 飞行计划验证结果 ==="]
        if self.valid:
            lines.append("状态: ✅ 通过")
        else:
            lines.append("状态: ❌ 未通过")
        if self.errors:
            lines.append(f"\n错误 ({len(self.errors)}):")
            for i, e in enumerate(self.errors, 1):
                lines.append(f"  {i}. {e}")
        if self.warnings:
            lines.append(f"\n警告 ({len(self.warnings)}):")
            for i, w in enumerate(self.warnings, 1):
                lines.append(f"  {i}. {w}")
        return "\n".join(lines)


class FlightPlanValidator:
    """
    飞行计划验证器
    ==============
    验证飞行计划的合法性，包括：
    1. 时间顺序正确
    2. 起飞/降落交替
    3. 电量不超限
    4. 载重不超限
    5. 航线合法（使用预设航线或飞往游轮）
    6. 换电只在陆地机场
    7. 装卸货逻辑正确
    """

    def __init__(
        self,
        map_data: Optional[MapData] = None,
        order_manager: Optional[OrderManager] = None,
    ):
        self.map_data = map_data or get_map_data()
        self.order_manager = order_manager or OrderManager()
        self.calculator = DroneFlightCalculator(self.map_data)

    def validate(self, plan: FlightPlan) -> ValidationResult:
        """
        验证飞行计划的合法性。

        按无人机分组验证，每个无人机的动作序列应该是：
        起飞 → 降落 → [换电] → 起飞 → 降落 → ...
        """
        result = ValidationResult()

        if not plan.actions:
            result.add_warning("飞行计划为空")
            return result

        # 按无人机分组（通过位置和动作序列推断）
        # 简化验证：检查整个动作序列
        actions = sorted(plan.actions, key=lambda a: a.time_seconds)

        prev_action = None
        loaded_orders: Dict[int, float] = {}  # order_id -> weight_kg
        current_payload = 0.0
        current_battery = 100.0
        current_location = ""
        battery_swap_time_remaining = 0.0

        for i, action in enumerate(actions):
            # 检查1: 时间顺序
            if prev_action and action.time_seconds < prev_action.time_seconds:
                result.add_error(f"动作{i+1}时间({action.time_str})早于前一个动作({prev_action.time_str})")

            if action.action == ActionType.TAKEOFF:
                # 检查2: 起飞前应该有降落（第一个动作除外）
                if prev_action and prev_action.action != ActionType.LAND:
                    result.add_error(f"动作{i+1}: 起飞前缺少降落动作")

                # 检查3: 装货载重
                if action.load_orders:
                    order_ids = [int(x.strip()) for x in action.load_orders.split(",") if x.strip()]
                    for oid in order_ids:
                        if oid in self.order_manager.orders:
                            order = self.order_manager.orders[oid]
                            loaded_orders[oid] = order.weight_kg
                            current_payload += order.weight_kg
                        else:
                            result.add_warning(f"动作{i+1}: 未知订单号 {oid}")

                    if current_payload > DRONE_MAX_PAYLOAD_KG:
                        result.add_error(
                            f"动作{i+1}: 载重超限 {current_payload:.1f}kg > {DRONE_MAX_PAYLOAD_KG}kg"
                        )

                # 检查4: 电量是否足够飞行到目标
                # 简化：需要下一个降落动作才能计算
                current_location = action.location

                # 如果参数列有坐标，说明飞往游轮
                if action.parameters:
                    # 验证参数格式
                    try:
                        parts = action.parameters.split(",")
                        if len(parts) != 3:
                            result.add_error(f"动作{i+1}: 参数格式错误，应为 经度,纬度,高度")
                        else:
                            float(parts[0])  # 经度
                            float(parts[1])  # 纬度
                            int(parts[2])    # 高度
                    except (ValueError, IndexError):
                        result.add_error(f"动作{i+1}: 参数格式错误")

            elif action.action == ActionType.LAND:
                # 检查5: 降落前应该有起飞
                if prev_action and prev_action.action != ActionType.TAKEOFF:
                    result.add_error(f"动作{i+1}: 降落前缺少起飞动作")

                # 检查6: 换电只能在陆地机场
                if action.swap_battery == "是":
                    if action.location not in [ap.name for ap in self.map_data.get_land_airports()]:
                        result.add_error(
                            f"动作{i+1}: 只能在陆地机场换电，{action.location}不可换电"
                        )

                # 检查7: 卸货逻辑
                if action.unload_orders:
                    order_ids = [int(x.strip()) for x in action.unload_orders.split(",") if x.strip()]
                    for oid in order_ids:
                        if oid in loaded_orders:
                            current_payload -= loaded_orders[oid]
                            del loaded_orders[oid]
                        else:
                            result.add_warning(f"动作{i+1}: 卸载未装载的订单 {oid}")

                # 更新位置
                current_location = action.location

            prev_action = action

        # 检查8: 所有已装载订单都应该被卸载
        if loaded_orders:
            result.add_warning(f"计划结束时仍有 {len(loaded_orders)} 个订单未卸载")

        return result

    def validate_battery_feasibility(
        self,
        actions: List[FlightAction],
    ) -> ValidationResult:
        """
        专门验证电量可行性。

        逐段计算每段飞行的电量消耗，检查是否会电量耗尽。
        """
        result = ValidationResult()

        battery = 100.0
        swap_count = 0

        i = 0
        while i < len(actions):
            action = actions[i]

            if action.action == ActionType.TAKEOFF:
                # 找到对应的降落动作
                landing = None
                for j in range(i + 1, len(actions)):
                    if actions[j].action == ActionType.LAND:
                        landing = actions[j]
                        break

                if landing is None:
                    result.add_error(f"起飞动作({action.time_str})没有对应的降落动作")
                    break

                # 计算飞行距离
                flight_dist_km = 0.0

                if action.parameters:
                    # 飞往游轮：使用参数中的坐标估算距离
                    try:
                        parts = action.parameters.split(",")
                        target = GeoCoord(float(parts[0]), float(parts[1]), int(parts[2]))
                        from_ap = self.map_data.get_airport(action.location)
                        from .map_data import horizontal_distance_meters
                        h_dist = horizontal_distance_meters(from_ap.coord, target) / 1000.0
                        takeoff_dist = max(0, DRONE_FLIGHT_ALTITUDE - from_ap.coord.alt) / 1000.0
                        landing_dist = max(0, DRONE_FLIGHT_ALTITUDE - target.alt) / 1000.0
                        flight_dist_km = takeoff_dist + h_dist + landing_dist
                    except (ValueError, IndexError):
                        result.add_warning(f"无法计算飞往游轮的距离")
                else:
                    # 使用预设航线
                    try:
                        flight_info = self.calculator.fly_between_nodes(
                            action.location, landing.location, battery
                        )
                        flight_dist_km = flight_info['total_distance_km']
                    except ValueError as e:
                        result.add_error(str(e))
                        i += 1
                        continue

                # 计算电量消耗
                battery_consumed = self.calculator.battery_consumption(flight_dist_km)
                battery -= battery_consumed

                if battery < 0:
                    result.add_error(
                        f"从{action.location}飞往{landing.location}电量不足: "
                        f"需要{battery_consumed:.1f}%, 剩余{battery + battery_consumed:.1f}%"
                    )

            elif action.action == ActionType.LAND:
                if action.swap_battery == "是":
                    battery = 100.0
                    swap_count += 1

            i += 1

        return result


# ============================================================
# 飞行计划导出
# ============================================================

class FlightPlanExporter:
    """
    飞行计划导出器
    ==============
    将飞行计划导出为Excel文件，符合仿真平台导入格式。
    """

    @staticmethod
    def export_to_excel(plan: FlightPlan, filepath: str):
        """
        导出飞行计划到Excel文件。

        格式要求：
        - 所有值为字符串格式
        - 时间格式 "分:秒" 如 "0:0"
        - 参数列：经度(6位小数),纬度(6位小数),高度(整数)
        - 装货/卸货列：只填订单号数字，不加"订单"等中文
        - 是否换电列：是/否
        """
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "学生实训导入策略模板"

        # 表头
        headers = ["时间(分:秒)", "起降场", "动作", "参数", "装货订单", "卸货订单", "是否换电", "备注"]
        ws.append(headers)

        # 数据行
        for row in plan.to_rows():
            ws.append(row)

        wb.save(filepath)
        return filepath
