"""
加班申请模型 - Overtime Request Model
=======================================

本模块定义加班申请数据表，记录员工加班的日期、时长、类型及审批状态，
并作为薪资计算模块（services/payroll.py）的数据来源之一。

业务背景
--------
加班管理是考勤与薪资计算的交叉模块：
  1. 员工提交加班申请，填写加班日期、时段和事由
  2. 申请进入审批流程，由直属上级或 HR 审批
  3. 审批通过后，薪资服务在月度结算时读取该月所有已批加班记录，
     按加班类型对应的系数折算加班费，计入当月薪资

加班类型与薪资系数
------------------
weekday  — 工作日加班，系数 1.5x（加班费 = 日薪/8 × 1.5 × 加班时长）
weekend  — 休息日加班，系数 2.0x（安排补休则不计费）
holiday  — 法定节假日加班，系数 3.0x（最高系数，不可用补休替代）

注意：薪资服务（services/payroll.py）直接读取 `hours` 字段和 `overtime_type`
字段计算加班费，因此 hours 的精确性直接影响薪资发放金额。

双状态字段设计
--------------
本表存在 status 和 approval_status 两个状态字段，语义不同：

  status（业务状态）
    描述申请记录当前所处的业务生命周期阶段：
    pending    — 已提交，等待审批
    approved   — 审批通过，加班时长计入薪资
    rejected   — 审批驳回
    cancelled  — 员工主动撤销申请

  approval_status（审批流状态）
    描述在通用审批引擎（ApprovalInstance）中的流转状态，
    与审批流模块保持一致（如：waiting/processing/approved/rejected）。
    当使用通用审批引擎时，两个字段值可能存在短暂不一致，
    审批流最终回调时将同步更新 status。

包含数据表
----------
- overtime_requests      加班申请记录
"""

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func

from app.core.database import Base


class OvertimeRequest(Base):
    """
    加班申请记录。

    一条记录对应一次加班事件，记录员工在某天某时段的加班安排和审批结果。
    审批通过的记录将被薪资服务（services/payroll.py）在月度结算时读取，
    自动折算对应类型的加班费。

    时间字段说明
    ------------
    overtime_date   — 加班发生的自然日（日期类型，不含时区）
    start_time      — 加班开始时间，格式"HH:MM"，如"18:30"
    end_time        — 加班结束时间，格式"HH:MM"，如"21:00"
    hours           — 实际计费加班时长（小数，精度0.1小时），可能与 end_time-start_time
                      存在差异（如扣除用餐时间），以 hours 字段为薪资计算依据

    加班类型（overtime_type）
    -------------------------
    weekday  — 工作日加班（周一至周五正常工作日延长工时），薪资系数 1.5x
    weekend  — 休息日加班（周六日），薪资系数 2.0x，部分情况可安排调休
    holiday  — 法定节假日加班（春节/国庆等），薪资系数 3.0x，不可调休

    审批字段
    --------
    approver_id   — 审批人员工 ID（直属上级或 HR），审批人离职后置 NULL
    approved_at   — 审批操作的时间戳（通过或驳回均记录）
    reject_reason — 驳回原因，仅在 status=rejected 时有值

    与薪资模块的集成
    ----------------
    services/payroll.py 在执行月度薪资计算时，查询条件为：
      WHERE employee_id = ? AND status = 'approved'
      AND overtime_date BETWEEN 月初 AND 月末
    查询结果按 overtime_type 分组累计 hours，分别乘以对应系数计入加班费。
    """

    __tablename__ = "overtime_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 申请人员工 ID，员工删除时级联删除其所有加班申请记录
    employee_id = Column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False, index=True, comment="申请人员工ID"
    )
    # 加班发生的自然日
    overtime_date = Column(Date, nullable=False, comment="加班日期")
    # 加班开始时间，字符串格式"HH:MM"
    start_time = Column(String(10), nullable=False, comment="开始时间 HH:MM")
    # 加班结束时间，字符串格式"HH:MM"
    end_time = Column(String(10), nullable=False, comment="结束时间 HH:MM")
    # 计费加班时长（小时），薪资计算直接依赖此字段，精度 0.1 小时
    hours = Column(Numeric(4, 1), nullable=False, comment="加班时长(小时)")
    # 加班类型，决定薪资系数：weekday(1.5x)/weekend(2.0x)/holiday(3.0x)
    overtime_type = Column(
        String(20), nullable=False, default="weekday", server_default="weekday",
        comment="加班类型: weekday/weekend/holiday"
    )
    # 加班事由，员工填写，供审批人参考
    reason = Column(Text, nullable=True, comment="加班事由")
    # 业务状态，描述申请在业务流程中的位置（见模块文档中的双状态设计说明）
    status = Column(
        String(20), nullable=False, default="pending", server_default="pending",
        comment="状态: pending/approved/rejected/cancelled"
    )
    # 审批流状态，与通用审批引擎（ApprovalInstance）保持同步；最终结果回写 status
    approval_status = Column(String(20), nullable=True, comment="审批状态")
    # 审批人员工 ID，可以是直属上级或 HR；审批人离职后置 NULL
    approver_id = Column(
        Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    # 审批操作时间，通过和驳回时均记录
    approved_at = Column(DateTime(timezone=True), nullable=True)
    # 驳回原因，status=rejected 时由审批人填写
    reject_reason = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now()
    )
