"""
系统操作审计日志模型 - Audit Log Model
=======================================

本模块定义系统操作日志表，用于记录所有关键业务操作的完整审计轨迹，
满足企业合规审计和安全溯源的要求。

业务背景
--------
审计日志（Audit Log）是企业信息系统的合规基础设施。对于人事管理系统，
以下类型的操作必须留有完整记录：
  - 员工信息变更（薪资调整、合同变更、职级晋升等敏感操作）
  - 权限授予和撤销
  - 审批流程中的关键节点（批准/驳回）
  - 登录行为（成功和失败，用于安全分析）
  - 系统自动化操作（如定时薪资结算、考勤汇总）

日志不可篡改原则
----------------
AuditLog 记录创建后不允许修改或删除（仅支持 INSERT，无 UPDATE/DELETE 接口）。
before_data 和 after_data 字段保存操作前后的完整 JSON 快照，
确保即使原始数据被修改，历史状态也可以完整还原。

系统自动操作
------------
当 operator_id = NULL 时，表示该操作由系统自动触发（非人工操作），
例如：每月自动薪资结算、定时考勤汇总、系统初始化等。
operator_name 同步为 NULL 或固定字符串"系统"。

包含数据表
----------
- audit_logs      系统操作审计日志
"""

from sqlalchemy import Column, DateTime, Integer, String, Text, func

from app.core.database import Base


class AuditLog(Base):
    """
    系统操作审计日志，记录所有关键操作的完整上下文快照。

    写入时机
    --------
    以下场景由对应服务层（service）在业务操作完成后同步写入审计日志：
      - 员工信息的新增（CREATE）、修改（UPDATE）、删除（DELETE）
      - 薪资记录的确认和发放
      - 审批节点的通过（APPROVE）和驳回（REJECT）
      - 用户登录（LOGIN）和登录失败（LOGIN_FAILED）
      - 权限变更（角色授予/撤销）

    操作类型（action）枚举值
    ------------------------
    CREATE        — 新建资源（如新增员工、创建合同）
    UPDATE        — 修改资源（如调薪、更新岗位信息）
    DELETE        — 删除资源（如撤销合同、删除候选人）
    APPROVE       — 审批通过（适用于所有审批流程节点）
    REJECT        — 审批驳回
    LOGIN         — 用户登录成功
    LOGIN_FAILED  — 用户登录失败（密码错误、账号锁定等）

    before_data / after_data 快照格式
    ----------------------------------
    两个字段均存储 JSON 字符串，分别表示操作前后的资源状态快照。
    例如调薪操作：
      before_data = '{"base_salary": 15000, "updated_at": "2024-01-01"}'
      after_data  = '{"base_salary": 18000, "updated_at": "2024-03-01"}'

    对于 CREATE 操作：before_data = NULL
    对于 DELETE 操作：after_data = NULL
    对于 LOGIN 操作：两者均为 NULL（无资源状态变化）

    模块（module）分类
    ------------------
    用于按模块筛选日志，值为系统模块标识：
    employee / payroll / leave / attendance / performance /
    recruitment / approval / training / competency / talent / system

    查询建议
    --------
    - 按 operator_id + created_at 范围查询：追踪特定用户的操作历史
    - 按 module + action + created_at 范围查询：合规审计报告
    - 按 resource_type + resource_id 查询：追踪某条记录的完整变更历史
    - created_at 已建立索引，支持高效的时间范围查询
    """

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 操作人员工 ID；NULL 表示系统自动操作（如定时任务触发的薪资结算）
    operator_id = Column(Integer, nullable=True, index=True, comment="操作人员工ID (NULL=系统)")
    # 操作人姓名快照，避免员工姓名变更后无法回溯历史记录的操作者身份
    operator_name = Column(String(50), nullable=True, comment="操作人姓名快照")
    # 操作类型，见类文档中的枚举值说明
    action = Column(
        String(50), nullable=False,
        comment="操作类型: CREATE/UPDATE/DELETE/APPROVE/REJECT/LOGIN等"
    )
    # 操作所属业务模块，用于按模块分组查询和展示
    module = Column(String(50), nullable=False, index=True, comment="所属模块: employee/payroll/leave等")
    # 被操作资源的数据库主键 ID（与 resource_type 配合使用唯一定位一条记录）
    resource_id = Column(Integer, nullable=True, comment="资源ID")
    # 被操作资源的类型名称，如"Employee"、"SalaryRecord"、"LeaveRequest"
    resource_type = Column(String(50), nullable=True, comment="资源类型")
    # 操作前资源状态的 JSON 快照；CREATE 操作时为 NULL
    before_data = Column(Text, nullable=True, comment="变更前数据JSON快照")
    # 操作后资源状态的 JSON 快照；DELETE 操作时为 NULL
    after_data = Column(Text, nullable=True, comment="变更后数据JSON快照")
    # 操作来源 IP 地址，用于安全分析（如异常地理位置登录检测）
    ip_address = Column(String(50), nullable=True, comment="操作IP")
    # 操作时间戳，建立索引以支持高效的时间范围查询
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
