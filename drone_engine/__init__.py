# 无人机配送仿真竞赛 - 基础计算引擎
# Drone Delivery Simulation Competition - Basic Computation Engine
"""
核心模块:
- map_data: 地图、机场、航线数据
- cruise_ship: 游轮轨迹计算
- drone: 无人机飞行计算
- order: 订单处理
- cost: 成本计算
- flight_plan: 飞行计划生成与验证
- scheduler: 旧版贪心调度器（保留参考）
- scheduler: 新版ALNS调度器
- optimizer: ALNS优化器包
  - config: ALNS参数配置
  - solution: 解的表示
  - evaluator: 解评估
  - construction: 初始解构造
  - destroy: 破坏算子
  - repair: 修复算子
  - alns: ALNS主引擎
"""