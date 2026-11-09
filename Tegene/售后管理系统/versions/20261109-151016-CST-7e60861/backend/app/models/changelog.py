"""
部门/职位变动日志模型 - Department Change Log Model
=====================================================

本模块定义员工组织架构变动的历史记录表，追踪员工在职期间的部门调动和职位变化，
为 HR 提供完整的员工职业发展轨迹。

业务背景
--------
员工在公司任职期间可能经历多次组织架构调整：
  - 跨部门调动（如从研发部调往产品部）
  - 职位变更（如从高级工程师调整为技术专家）
  - 晋升（职级提升，通常伴随薪资调整）
  - 降职（职级降低，较少见）

这些变动历史对以下场景至关重要：
  1. 员工个人档案：展示完整的职业成长路径
  2. 组织分析：统计各部门的人员流入流出，评估组织健康度
  3. 合规审查：回溯特定时间点的组织结构，用于劳动仲裁或审计
  4. 薪资历史关联：将调薪与对应的职级/职位变动关联

自动写入时机
------------
本表记录由服务层在以下操作中自动写入，不需要 HR 手动维护：
  - 单个员工调岗（PUT /employees/{id}，当 department_id 或 position 变化时）
  - 批量调岗（POST /employees/bulk-transfer，同时调整多名员工部门）
  - 职级晋升/降职（专用接口，同时触发薪资调整）

effective_date 说明
-------------------
effective_date 记录变动正式生效的时间点（而非数据写入时间），例如：
某员工 3 月 15 日申请调岗，4 月 1 日生效，
则 effective_date = 2024-04-01 00:00:00+00:00，
created_at = 2024-03-15 10:30:00+00:00。

包含数据表
----------
- department_change_logs      员工部门/职位变动历史
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

from app.core.database import Base


class DepartmentChangeLog(Base):
    """
    员工部门/职位变动历史记录。

    每条记录对应一次完整的组织架构变动事件，记录变动前后的部门、职位和职级信息。
    服务层在执行调岗/晋升操作时自动创建，只写不改（append-only）。

    变动类型（change_type）
    -----------------------
    department_transfer — 跨部门调动（from_department_id ≠ to_department_id），
                          职位可能随之变化，也可能保持不变
    position_change     — 同部门内职位名称变更（如从"后端工程师"改为"系统架构师"），
                          不涉及职级和部门变化
    promotion           — 职级晋升（from_job_level < to_job_level），
                          通常伴随职位名称和薪资调整
    demotion            — 职级降低（from_job_level > to_job_level），
                          较为罕见，需要有管理层审批记录支撑

    变动前后字段说明
    ----------------
    from_department_id / to_department_id
        变动前后的部门主键；外键关联 departments 表；部门撤销后置 NULL
        两者均为 NULL 时表示数据不完整（应避免）

    from_position / to_position
        职位名称字符串快照（非外键），允许职位管理未在系统中维护的情况

    from_job_level / to_job_level
        职级字符串快照（如 "P5"、"M3"、"高级工程师"），用于晋降职的对比显示

    operator_id
        执行此次调动操作的员工 ID（通常是 HR 或管理员），用于责任追溯；
        操作人离职后置 NULL，变动记录本身保留

    查询建议
    --------
    - 按 employee_id 查询所有记录（按 effective_date 升序）：获取员工完整职业路径
    - 按 to_department_id + effective_date 查询：统计某部门某时段人员流入
    - 按 from_department_id + effective_date 查询：统计某部门某时段人员流出
    - 按 change_type = 'promotion' 查询：统计晋升人数和晋升率
    """

    __tablename__ = "department_change_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 发生变动的员工 ID；员工物理删除时级联删除其变动记录（实际业务通常不删员工）
    employee_id = Column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False, index=True, comment="员工ID"
    )
    # 变动类型：department_transfer/position_change/promotion/demotion
    change_type = Column(
        String(30), nullable=False,
        comment="变动类型: department_transfer/position_change/promotion/demotion"
    )
    # 变动前所在部门；部门撤销后置 NULL，但变动历史记录仍然保留
    from_department_id = Column(
        Integer, ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    # 变动后所在部门；同上
    to_department_id = Column(
        Integer, ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    # 变动前职位名称快照（字符串，非外键），如"后端工程师"
    from_position = Column(String(100), nullable=True)
    # 变动后职位名称快照，如"高级后端工程师"
    to_position = Column(String(100), nullable=True)
    # 变动前职级快照，如"P5"、"M2"
    from_job_level = Column(String(50), nullable=True)
    # 变动后职级快照，如"P6"、"M3"
    to_job_level = Column(String(50), nullable=True)
    # 变动正式生效时间（可能晚于记录创建时间，即提前录入待生效的调动）
    effective_date = Column(DateTime(timezone=True), nullable=False, comment="生效时间")
    # 操作人（HR 或管理员）员工 ID，用于追溯"谁录入了这条变动记录"
    operator_id = Column(
        Integer, ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True, comment="操作人ID"
    )
    # 变动备注，如"因业务需要支援新业务线"、"绩效提升晋升 P6"
    remark = Column(Text, nullable=True)
    # 记录写入时间（与 effective_date 区分：写入时间是操作时间，生效时间是业务时间）
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
