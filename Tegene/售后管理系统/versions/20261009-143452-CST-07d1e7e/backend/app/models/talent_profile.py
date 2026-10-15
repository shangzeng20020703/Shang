"""
员工技能档案模型 - Employee Skill Profile Models
=================================================

本模块定义员工技能矩阵（Skill Matrix）相关的数据表，用于支撑人才发展和人才盘点模块。

业务背景
--------
技能档案（Talent Profile）是人才盘点体系的基础数据支撑。HR 或员工本人填写技能条目，
记录员工在各类技能上的名称、分类和熟练程度，形成可横向比较的「技能矩阵」。

技能矩阵的应用场景：
  1. 人才搜索（/talent-profile/search API）：按技能名称+熟练度检索具备特定能力的员工
  2. 人才盘点：在盘点会议中展示员工技能概况，辅助九宫格定位
  3. 继任计划：评估候选人是否具备接任关键岗位所需的核心技能
  4. 培训规划：识别技能缺口，定向安排培训课程

与 EmployeePersonalSkill 的区别
--------------------------------
本模块的 EmployeeSkill 是「人才系统技能矩阵」中的标准化技能条目：
  - 由 HR 或直属上级填写，经过统一的分类和评级标准
  - 用于跨员工横向比较和搜索
  - proficiency_level 采用 1-5 标准化评分

EmployeePersonalSkill（位于 employee.py）是「员工入职登记表」中的自述技能：
  - 由员工本人填写，格式相对自由
  - 主要用于个人档案展示，不参与横向对比分析
  - 通常在招聘入职阶段填写，后续较少更新

包含数据表
----------
- employee_skills      员工技能条目（技能矩阵数据行）
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class EmployeeSkill(Base):
    """
    员工技能条目，构成员工个人技能矩阵的最小单元。

    每条记录代表某员工在某项技能上的能力登记，包含技能名称、所属分类和
    当前熟练程度。同一员工可拥有任意数量的技能条目，所有条目合并即为
    该员工的技能矩阵视图。

    技能分类（skill_category）
    --------------------------
    技术   — 编程语言、框架、算法、硬件工程、机器人控制等技术类技能
    管理   — 项目管理、团队管理、战略规划、OKR 等管理类技能
    语言   — 中文、英语、日语等语言类技能
    工具   — Office、CAD、ERP、Jira 等软件工具使用技能

    熟练程度（proficiency_level）评分说明
    --------------------------------------
    1 = 了解（Awareness）      — 知道概念，未实际使用过
    2 = 初级（Beginner）       — 在指导下可完成基础任务
    3 = 中级（Intermediate）   — 能独立完成常规任务
    4 = 高级（Advanced）       — 能处理复杂场景，可指导他人
    5 = 专家（Expert）         — 行业级专家，可制定标准和最佳实践

    数据维护说明
    ------------
    - 同一员工对同一技能名称可能存在多条记录（不同时期更新），
      查询时通常取最新一条（按 updated_at 降序）。
    - 员工离职时级联删除其所有技能条目（ondelete="CASCADE"）。
    - updated_at 字段在每次修改熟练度时自动刷新，用于追踪技能成长轨迹。

    搜索接口
    --------
    /talent-profile/search 端点基于本表做全文搜索和熟练度过滤，
    支持按 skill_name（模糊匹配）+ proficiency_level（最小值过滤）
    返回符合条件的员工列表。
    """

    __tablename__ = "employee_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 员工外键，员工删除时级联清除其所有技能记录
    employee_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 技能名称，如"Python"、"项目管理"、"英语"、"AutoCAD"
    skill_name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="技能名称"
    )
    # 技能分类，用于在技能矩阵中按类别分组展示
    skill_category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="技能分类: 技术/管理/语言/工具",
    )
    # 熟练度评分 1-5，含义见类文档
    proficiency_level: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="熟练度(1-5): 1=了解 2=初级 3=中级 4=高级 5=专家"
    )
    # 创建时间，记录技能首次登记的时间点
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # 更新时间，每次熟练度变更时自动更新，可用于追踪技能进步速度
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # 关联员工对象，selectin 加载避免列表查询时的 N+1 问题
    employee: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", lazy="selectin"
    )
