"""
猎头管理服务层 (Headhunter Service Layer)
=========================================

本模块负责猎头相关的所有业务逻辑，包含两个核心服务类：

1. HeadhunterService — 猎头公司/顾问管理
   - 猎头机构的增删改查（CRUD）
   - 支持按名称/联系人关键字搜索、按状态筛选
   - 软删除（通过 delete 方法物理删除，调用方可根据需求改为软删除）

2. RecommendationService — 猎头推荐候选人记录管理
   - 管理猎头提交的候选人推荐记录（HeadhunterRecommendation）
   - 支持按猎头公司、推荐状态筛选
   - 用于跟踪候选人从推荐到入职的全流程进展及付款情况

数据模型依赖：
  - app.models.headhunter.Headhunter — 猎头公司/顾问实体
  - app.models.headhunter.HeadhunterRecommendation — 猎头推荐记录

调用关系：
  API 层 (api/v1/headhunter.py) → HeadhunterService / RecommendationService → DB

注意事项：
  - 所有数据库操作使用 SQLAlchemy 2.0 异步接口（AsyncSession）
  - 方法均为静态方法（@staticmethod），服务类不持有状态
  - db.flush() 后需 db.refresh() 以获取数据库自动生成的字段（id、created_at 等）
  - 删除操作返回 bool，调用方根据返回值决定是否返回 404
"""

from typing import Optional, Tuple, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.headhunter import Headhunter, HeadhunterRecommendation
from app.schemas.headhunter import (
    HeadhunterCreate, HeadhunterUpdate,
    RecommendationCreate, RecommendationUpdate,
)


class HeadhunterService:
    """
    猎头公司/顾问管理服务。

    提供对 Headhunter 实体的完整 CRUD 操作，支持分页、关键字搜索和状态筛选。
    """

    @staticmethod
    async def get_list(
        db: AsyncSession,
        *,
        keyword: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[int, List[Headhunter]]:
        """
        分页查询猎头列表。

        Args:
            db: 异步数据库会话。
            keyword: 可选关键字，模糊匹配猎头公司名称（name）或联系人（contact_person）。
            status: 可选状态筛选，如 "active" / "inactive"。
            page: 当前页码，从 1 开始。
            page_size: 每页记录数，默认 20。

        Returns:
            (total, items) 元组：
              - total (int): 符合条件的总记录数（用于前端分页计算）。
              - items (List[Headhunter]): 当前页的猎头对象列表，按创建时间倒序排列。
        """
        stmt = select(Headhunter)
        # 关键字同时匹配公司名称和联系人姓名
        if keyword:
            stmt = stmt.where(
                Headhunter.name.ilike(f"%{keyword}%")
                | Headhunter.contact_person.ilike(f"%{keyword}%")
            )
        if status:
            stmt = stmt.where(Headhunter.status == status)
        stmt = stmt.order_by(Headhunter.created_at.desc())

        # 先查总数（在分页之前，基于完整过滤条件的子查询）
        total_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
        total = total_result.scalar_one()

        # 再查分页数据
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        items = list(result.scalars().all())
        return total, items

    @staticmethod
    async def get_by_id(db: AsyncSession, headhunter_id: int) -> Optional[Headhunter]:
        """
        按主键查询单个猎头记录。

        Args:
            db: 异步数据库会话。
            headhunter_id: 猎头记录主键 ID。

        Returns:
            对应的 Headhunter 对象，若不存在则返回 None（由调用方决定是否抛 404）。
        """
        result = await db.execute(select(Headhunter).where(Headhunter.id == headhunter_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def create(db: AsyncSession, data: HeadhunterCreate) -> Headhunter:
        """
        创建新猎头记录。

        Args:
            db: 异步数据库会话。
            data: 猎头创建 DTO（HeadhunterCreate），包含公司名、联系人、联系方式、状态等字段。

        Returns:
            已持久化的 Headhunter 对象（含数据库自动生成的 id、created_at）。
        """
        obj = Headhunter(**data.model_dump())
        db.add(obj)
        await db.flush()       # 将 INSERT 写入事务（未提交），获取自动生成的主键
        await db.refresh(obj)  # 从数据库刷新所有字段（包括 created_at 等服务端默认值）
        return obj

    @staticmethod
    async def update(db: AsyncSession, headhunter_id: int, data: HeadhunterUpdate) -> Optional[Headhunter]:
        """
        更新猎头记录（局部更新，仅修改请求中包含的字段）。

        Args:
            db: 异步数据库会话。
            headhunter_id: 要更新的猎头记录主键 ID。
            data: 猎头更新 DTO（HeadhunterUpdate），使用 exclude_unset=True 只更新提交的字段。

        Returns:
            更新后的 Headhunter 对象；若记录不存在则返回 None。
        """
        obj = await HeadhunterService.get_by_id(db, headhunter_id)
        if obj is None:
            return None
        # exclude_unset=True 确保只更新客户端显式传入的字段，避免用 None 覆盖已有值
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(obj, field, value)
        await db.flush()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def delete(db: AsyncSession, headhunter_id: int) -> bool:
        """
        物理删除猎头记录。

        Args:
            db: 异步数据库会话。
            headhunter_id: 要删除的猎头记录主键 ID。

        Returns:
            True 表示删除成功；False 表示记录不存在（调用方可据此返回 404）。

        注意：
            当前为物理删除（直接从数据库移除），如业务需要软删除（标记 is_active=False），
            请修改此方法实现。删除前须确保关联的 HeadhunterRecommendation 记录已处理，
            否则可能触发外键约束错误。
        """
        obj = await HeadhunterService.get_by_id(db, headhunter_id)
        if obj is None:
            return False
        await db.delete(obj)
        await db.flush()
        return True


class RecommendationService:
    """
    猎头推荐候选人记录管理服务。

    管理猎头提交的候选人推荐记录（HeadhunterRecommendation），涵盖：
    - 推荐记录的创建、更新、删除
    - 按猎头公司或候选人状态筛选
    - 分页查询

    数据流：
        猎头提交候选人简历 → 创建 HeadhunterRecommendation →
        HR 审核推进状态 → 更新 status 字段 →
        入职后触发付款流程（付款逻辑在 payment 模块或外部处理）
    """

    @staticmethod
    async def get_list(
        db: AsyncSession,
        *,
        headhunter_id: Optional[int] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[int, List[HeadhunterRecommendation]]:
        """
        分页查询猎头推荐记录。

        Args:
            db: 异步数据库会话。
            headhunter_id: 可选，按猎头公司 ID 筛选，只返回该猎头提交的推荐记录。
            status: 可选，按推荐状态筛选（如 "pending"/"interviewed"/"offered"/"onboarded"/"rejected"）。
            page: 当前页码，从 1 开始。
            page_size: 每页记录数，默认 20。

        Returns:
            (total, items) 元组：
              - total (int): 符合条件的总记录数。
              - items (List[HeadhunterRecommendation]): 当前页推荐记录列表，按创建时间倒序。
        """
        stmt = select(HeadhunterRecommendation)
        if headhunter_id:
            stmt = stmt.where(HeadhunterRecommendation.headhunter_id == headhunter_id)
        if status:
            stmt = stmt.where(HeadhunterRecommendation.status == status)
        stmt = stmt.order_by(HeadhunterRecommendation.created_at.desc())

        # 先统计总数（子查询方式，保证过滤条件与列表查询一致）
        total_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
        total = total_result.scalar_one()

        # 分页查询
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        items = list(result.scalars().all())
        return total, items

    @staticmethod
    async def create(db: AsyncSession, data: RecommendationCreate) -> HeadhunterRecommendation:
        """
        创建猎头推荐候选人记录。

        Args:
            db: 异步数据库会话。
            data: 推荐记录创建 DTO（RecommendationCreate），包含：
                  - headhunter_id: 所属猎头公司 ID（外键）
                  - candidate_name: 候选人姓名
                  - position: 推荐岗位
                  - status: 初始状态（通常为 "pending"）
                  - 以及其他候选人信息字段

        Returns:
            已持久化的 HeadhunterRecommendation 对象（含自动生成的 id、created_at）。

        注意：
            调用方需确保 headhunter_id 对应的猎头公司存在，否则数据库外键约束会报错。
        """
        obj = HeadhunterRecommendation(**data.model_dump())
        db.add(obj)
        await db.flush()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def update(
        db: AsyncSession, rec_id: int, data: RecommendationUpdate
    ) -> Optional[HeadhunterRecommendation]:
        """
        更新猎头推荐记录（局部更新）。

        常用场景：
          - HR 推进候选人状态（如从 "pending" 改为 "interviewed"）
          - 记录面试/Offer/入职等关键节点信息
          - 更新付款相关字段

        Args:
            db: 异步数据库会话。
            rec_id: 推荐记录主键 ID。
            data: 推荐记录更新 DTO（RecommendationUpdate），使用 exclude_unset=True 局部更新。

        Returns:
            更新后的 HeadhunterRecommendation 对象；若记录不存在则返回 None。
        """
        result = await db.execute(
            select(HeadhunterRecommendation).where(HeadhunterRecommendation.id == rec_id)
        )
        obj = result.scalar_one_or_none()
        if obj is None:
            return None
        # 只更新客户端显式传入的字段
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(obj, field, value)
        await db.flush()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def delete(db: AsyncSession, rec_id: int) -> bool:
        """
        物理删除猎头推荐记录。

        Args:
            db: 异步数据库会话。
            rec_id: 推荐记录主键 ID。

        Returns:
            True 表示删除成功；False 表示记录不存在。

        注意：
            物理删除会永久移除记录，建议在删除前确认该记录无关联付款记录，
            避免产生悬空的财务数据。
        """
        result = await db.execute(
            select(HeadhunterRecommendation).where(HeadhunterRecommendation.id == rec_id)
        )
        obj = result.scalar_one_or_none()
        if obj is None:
            return False
        await db.delete(obj)
        await db.flush()
        return True
