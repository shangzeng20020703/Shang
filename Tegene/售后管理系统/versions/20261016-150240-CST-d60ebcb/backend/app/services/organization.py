"""
组织架构 Service — 工作地点（Location）与部门（Department）的业务逻辑层

架构位置：
    API 层 (api/v1/organization.py) → LocationService / DepartmentService
    Service 层（本文件）→ SQLAlchemy ORM Model (models/organization.py)

业务职责：
    1. LocationService   - 工作地点（城市/园区）的 CRUD，支持软删除
    2. DepartmentService - 部门树结构的 CRUD，支持递归树形返回、软删除

模型关系：
    - Location  ←── Department (Department.location_id → Location.id)
    - Department ←── Department (Department.parent_id → Department.id，自引用树形结构)
    - Department ←── Employee   (Employee.department_id → Department.id)

部门树说明：
    - Department 通过 parent_id 自引用构成任意深度的树形结构（父部门为 None 时为根节点）
    - get_tree() 一次加载所有节点，在 Python 内存中用字典构建树（避免递归 SQL）
    - DepartmentTreeNode schema 含 children 列表字段，API 层直接返回嵌套 JSON

软删除说明：
    - Location.delete 和 Department.delete 均为软删除（设 is_active=False）
    - 默认查询过滤 is_active=False 的记录（include_inactive=False）
    - 删除部门前检查是否有活跃子部门（防止树结构孤立）

依赖关系：
    - app.models.organization: Department, Location
    - app.schemas.organization: DepartmentCreate/Update/TreeNode, LocationCreate/Update

被哪些 API 端点调用：
    - GET/POST/PUT/DELETE /api/v1/organizations/locations/*  → LocationService
    - GET/POST/PUT/DELETE /api/v1/departments/*             → DepartmentService
    - GET /api/v1/departments/tree                          → DepartmentService.get_tree

架构约定：
    - 所有方法以 db: AsyncSession 为第一参数，不自行 commit
    - 业务校验失败抛出 HTTPException（400/404）
"""
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.organization import Department, Location, OrgCompanyLayout
from app.models.payroll import Company
from app.schemas.organization import (
    DepartmentCreate,
    DepartmentTreeNode,
    DepartmentUpdate,
    LocationCreate,
    LocationUpdate,
    OrgCompanyLayoutUpdate,
)


# ==================== Location Service ====================

class LocationService:
    """
    工作地点 Service。

    管理公司各工作地点（如"上海总部"、"北京分公司"、"深圳研发中心"等）。
    地点用于关联员工（Employee.location_id）和部门（Department.location_id），
    是组织架构的基础维度之一。

    软删除：delete 方法将 is_active 设为 False，不物理删除记录，
    已关联该地点的员工和部门数据不受影响。

    被调用端点：/api/v1/organizations/locations/*
    """

    @staticmethod
    async def get_all(db: AsyncSession, include_inactive: bool = False) -> List[Location]:
        """
        获取所有工作地点列表，默认只返回启用状态（is_active=True）的地点。

        参数：
            db               - 异步数据库会话
            include_inactive - 是否包含已停用地点（默认 False，前端一般只需活跃地点）

        返回：
            Location 列表，按 id 升序排列
        """
        stmt = select(Location).order_by(Location.id)
        if not include_inactive:
            stmt = stmt.where(Location.is_active == True)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_by_id(db: AsyncSession, location_id: int) -> Location:
        """
        按主键 ID 获取工作地点详情。

        参数：
            db          - 异步数据库会话
            location_id - 工作地点主键 ID

        返回：
            Location ORM 对象

        异常：
            HTTPException(404) - 工作地点不存在
        """
        result = await db.execute(
            select(Location).where(Location.id == location_id)
        )
        location = result.scalar_one_or_none()
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"工作地点 ID={location_id} 不存在",
            )
        return location

    @staticmethod
    async def create(db: AsyncSession, data: LocationCreate) -> Location:
        """
        创建工作地点。

        业务规则：
        - 地点名称全局唯一（不区分停用状态），重复则 400

        参数：
            db   - 异步数据库会话
            data - LocationCreate schema（含 name 等字段）

        返回：
            新建的 Location ORM 对象

        异常：
            HTTPException(400) - 地点名称已存在
        """
        # 检查名称重复（包含已停用的地点，防止同名地点被重复创建后再启用）
        exists = await db.execute(
            select(func.count()).select_from(Location).where(Location.name == data.name)
        )
        if exists.scalar() > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"工作地点名称 '{data.name}' 已存在",
            )
        location = Location(**data.model_dump())
        db.add(location)
        await db.flush()
        await db.refresh(location)
        return location

    @staticmethod
    async def update(
        db: AsyncSession, location_id: int, data: LocationUpdate
    ) -> Location:
        """
        更新工作地点信息（部分更新，exclude_unset=True）。

        参数：
            db          - 异步数据库会话
            location_id - 工作地点主键 ID
            data        - LocationUpdate schema（仅含需更新的字段）

        返回：
            更新后的 Location ORM 对象

        异常：
            HTTPException(404) - 工作地点不存在
        """
        location = await LocationService.get_by_id(db, location_id)
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(location, field, value)
        await db.flush()
        await db.refresh(location)
        return location

    @staticmethod
    async def delete(db: AsyncSession, location_id: int) -> None:
        """
        软删除工作地点：将 is_active 设为 False，不物理删除记录。

        注意：此操作不校验是否有员工或部门关联到该地点，
        已关联的员工/部门数据不受影响（仅在新查询中默认被过滤）。

        参数：
            db          - 异步数据库会话
            location_id - 工作地点主键 ID

        异常：
            HTTPException(404) - 工作地点不存在
        """
        location = await LocationService.get_by_id(db, location_id)
        location.is_active = False  # 软删除：不物理删除，保留历史关联
        await db.flush()


# ==================== Department Service ====================

class DepartmentService:
    """
    部门 Service。

    管理公司部门的完整生命周期，支持树形结构（任意深度）。

    部门树结构：
        - Department.parent_id 自引用：parent_id=None 的节点为一级部门（根节点）
        - 支持多级嵌套（如：集团 → 事业部 → 部门 → 小组）
        - get_tree() 一次查询所有节点，内存中构建树，效率优于递归 SQL

    部门编码（code）：
        - 格式为 D001、D002...（由系统自动生成，也可手动指定）
        - 全局唯一，创建和更新时均需校验唯一性
        - 自动生成逻辑：取当前部门总数 +1 作为序号，如有冲突则自增至无冲突

    软删除约束：
        - 删除前检查是否有活跃子部门，若有则拒绝删除
        - delete 方法仅设 is_active=False，不删除记录

    防循环约束：
        - update 时若 parent_id 被设为部门自身 ID，则拒绝（防止自环）
        - 注意：暂未防止跨层循环（A→B→C→A），由上层约束保证

    被调用端点：/api/v1/departments/*
    """

    @staticmethod
    async def _sync_subtree_levels(
        db: AsyncSession,
        root_id: int,
        root_level: int,
    ) -> None:
        """按父子关系重算整棵子树 level，确保层级逻辑由树结构驱动。"""
        result = await db.execute(select(Department))
        departments = list(result.scalars().all())
        children_map: dict[int, list[Department]] = {}
        for item in departments:
            if item.parent_id is not None:
                children_map.setdefault(item.parent_id, []).append(item)

        if root_level > 4:
            raise HTTPException(400, "组织架构最多支持四级")
        stack: list[tuple[int, int]] = [(root_id, root_level)]
        while stack:
            parent_id, parent_level = stack.pop()
            for child in children_map.get(parent_id, []):
                expected_level = parent_level + 1
                if expected_level > 4:
                    raise HTTPException(400, "调整后子部门将超过四级，请先调整子部门")
                if child.level != expected_level:
                    child.level = expected_level
                stack.append((child.id, expected_level))

    @staticmethod
    async def get_list(
        db: AsyncSession,
        location_id: Optional[int] = None,
        parent_id: Optional[int] = None,
        include_inactive: bool = False,
    ) -> List[Department]:
        """
        获取部门平铺列表（非树形），支持按地点、上级部门筛选。

        适用场景：需要展示某地点或某父部门下的直属子部门列表时使用。
        如需完整树形结构，请使用 get_tree()。

        参数：
            db               - 异步数据库会话
            location_id      - 按工作地点 ID 筛选（可选）
            parent_id        - 按上级部门 ID 筛选（可选，传 0 可筛选根节点）
            include_inactive - 是否包含已停用部门（默认 False）

        返回：
            Department 列表，按 sort_order 升序、id 升序排列
        """
        stmt = select(Department).order_by(Department.sort_order, Department.id)
        if not include_inactive:
            stmt = stmt.where(Department.is_active == True)
        if location_id is not None:
            stmt = stmt.where(Department.location_id == location_id)
        if parent_id is not None:
            stmt = stmt.where(Department.parent_id == parent_id)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_by_id(db: AsyncSession, department_id: int) -> Department:
        """
        按主键 ID 获取部门详情，同时预加载 location 关联。

        参数：
            db            - 异步数据库会话
            department_id - 部门主键 ID

        返回：
            Department ORM 对象（含 location 关联数据）

        异常：
            HTTPException(404) - 部门不存在
        """
        result = await db.execute(
            select(Department)
            .options(selectinload(Department.location))  # 预加载地点，避免 N+1
            .where(Department.id == department_id)
        )
        dept = result.scalar_one_or_none()
        if not dept:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"部门 ID={department_id} 不存在",
            )
        return dept

    @staticmethod
    async def create(db: AsyncSession, data: DepartmentCreate) -> Department:
        """
        创建部门。

        部门编码生成逻辑：
        - 若 data.code 为空：自动生成（D + 3位序号，如 D001、D002），最多尝试100次防冲突
        - 若 data.code 非空：校验唯一性，重复则 400

        参数：
            db   - 异步数据库会话
            data - DepartmentCreate schema（含 name、parent_id、location_id、code 等）

        返回：
            新建的 Department ORM 对象

        异常：
            HTTPException(400) - 部门编码已存在 / 上级部门不存在 / 工作地点不存在

        下游调用：
            get_by_id()（间接，通过 selectinload 预加载）
        """
        # 自动生成4位部门编码（如 D001）
        code = data.code
        if not code:
            # 以当前部门总数 +1 作为初始序号
            max_res = await db.execute(
                select(func.count()).select_from(Department)
            )
            seq = (max_res.scalar() or 0) + 1
            code = f"D{seq:03d}"
            # 防冲突：如果已存在，递增序号直到找到可用编码（最多尝试100次）
            for _ in range(100):
                chk = await db.execute(
                    select(func.count()).select_from(Department).where(Department.code == code)
                )
                if chk.scalar() == 0:
                    break
                seq += 1
                code = f"D{seq:03d}"
        else:
            # 手动指定编码：校验唯一性
            exists = await db.execute(
                select(func.count()).select_from(Department).where(Department.code == code)
            )
            if exists.scalar() > 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"部门编码 '{code}' 已存在",
                )

        # 验证上级部门存在（若指定了 parent_id）
        parent_department = None
        if data.parent_id is not None:
            parent = await db.execute(
                select(Department).where(Department.id == data.parent_id)
            )
            parent_department = parent.scalar_one_or_none()
            if parent_department is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"上级部门 ID={data.parent_id} 不存在",
                )

        # 验证公司主体存在（若指定了 company_id）
        if data.company_id is not None:
            company = await db.execute(
                select(Company).where(Company.id == data.company_id)
            )
            if company.scalar_one_or_none() is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"公司 ID={data.company_id} 不存在",
                )

        # 父部门存在时，默认继承父部门的公司归属，并校验不能跨公司挂载
        resolved_company_id = data.company_id
        if parent_department is not None:
            if parent_department.company_id is not None:
                if resolved_company_id is None:
                    resolved_company_id = parent_department.company_id
                elif resolved_company_id != parent_department.company_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="部门所属公司必须与上级部门一致",
                    )

        # 验证工作地点存在（若指定了 location_id）
        if data.location_id is not None:
            loc = await db.execute(
                select(Location).where(Location.id == data.location_id)
            )
            if loc.scalar_one_or_none() is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"工作地点 ID={data.location_id} 不存在",
                )

        dept_data = data.model_dump()
        # level 由父子关系自动计算，忽略前端传值
        dept_data.pop("level", None)
        dept_data["level"] = (parent_department.level + 1) if parent_department is not None else 1
        if dept_data["level"] > 4:
            raise HTTPException(400, "组织架构最多支持四级")
        dept_data["company_id"] = resolved_company_id
        dept_data["code"] = code  # 使用生成或验证后的编码
        dept = Department(**dept_data)
        db.add(dept)
        await db.flush()
        await db.refresh(dept)
        return dept

    @staticmethod
    async def update(
        db: AsyncSession, department_id: int, data: DepartmentUpdate
    ) -> Department:
        """
        更新部门信息（部分更新，exclude_unset=True）。

        业务校验：
        1. 若更新 code，需检查新编码唯一性（排除自身）
        2. 若更新 parent_id，需检查不能设为自身（防止自环）

        注意：暂未防止跨层循环（如 A→B→C→A 循环），由前端约束。

        参数：
            db            - 异步数据库会话
            department_id - 目标部门主键 ID
            data          - DepartmentUpdate schema（仅含需更新的字段）

        返回：
            更新后的 Department ORM 对象

        异常：
            HTTPException(404) - 部门不存在
            HTTPException(400) - 新编码已存在 / 将部门设为自身的上级
        """
        dept = await DepartmentService.get_by_id(db, department_id)
        update_data = data.model_dump(exclude_unset=True)
        # level 由树关系维护，不允许外部直接改
        update_data.pop("level", None)

        # 如果修改编码，需检查唯一性（排除自身的当前编码）
        if "code" in update_data and update_data["code"] != dept.code:
            exists = await db.execute(
                select(func.count())
                .select_from(Department)
                .where(Department.code == update_data["code"])
            )
            if exists.scalar() > 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"部门编码 '{update_data['code']}' 已存在",
                )

        # 防止循环引用：检查 parent_id 不能是自身，也不能是自身的子孙
        if "parent_id" in update_data and update_data["parent_id"] is not None:
            new_parent_id = update_data["parent_id"]
            if new_parent_id == department_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="不能将部门设为自身的上级",
                )
            # 沿 parent_id 链向上回溯，检测是否会形成环 (A→B→...→A)
            visited = {department_id}
            cursor_id = new_parent_id
            while cursor_id is not None:
                if cursor_id in visited:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="不能将部门设为自身子部门的下级（检测到循环引用）",
                    )
                visited.add(cursor_id)
                parent_result = await db.execute(
                    select(Department.parent_id).where(Department.id == cursor_id)
                )
                cursor_id = parent_result.scalar_one_or_none()

        # 校验公司主体存在
        if "company_id" in update_data and update_data["company_id"] is not None:
            company = await db.execute(
                select(Company).where(Company.id == update_data["company_id"])
            )
            if company.scalar_one_or_none() is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"公司 ID={update_data['company_id']} 不存在",
                )

        # 如修改上级部门，需保持公司归属一致；若未显式提供 company_id，则继承新父部门的 company_id
        if "parent_id" in update_data:
            new_parent_id = update_data["parent_id"]
            if new_parent_id is not None:
                parent_result = await db.execute(
                    select(Department).where(Department.id == new_parent_id)
                )
                new_parent = parent_result.scalar_one_or_none()
                if new_parent is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"上级部门 ID={new_parent_id} 不存在",
                    )
                target_company_id = update_data.get("company_id", dept.company_id)
                if new_parent.company_id is not None:
                    if target_company_id is None:
                        update_data["company_id"] = new_parent.company_id
                    elif target_company_id != new_parent.company_id:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="部门所属公司必须与上级部门一致",
                        )
                update_data["level"] = new_parent.level + 1
            elif "company_id" not in update_data:
                # 脱离原父节点时，默认保留当前 company_id
                update_data["company_id"] = dept.company_id
                update_data["level"] = 1
            else:
                update_data["level"] = 1
        elif "company_id" in update_data and dept.parent_id is not None:
            # 未变更父节点但修改公司时，也要校验与现有父节点一致
            parent_result = await db.execute(
                select(Department).where(Department.id == dept.parent_id)
            )
            parent = parent_result.scalar_one_or_none()
            if parent and parent.company_id is not None and update_data["company_id"] != parent.company_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="部门所属公司必须与上级部门一致",
                )

        for field, value in update_data.items():
            setattr(dept, field, value)
        await db.flush()
        if "parent_id" in update_data:
            await DepartmentService._sync_subtree_levels(db, dept.id, dept.level)
            await db.flush()
        await db.refresh(dept)
        return dept

    @staticmethod
    async def delete(db: AsyncSession, department_id: int) -> None:
        """
        软删除部门：将 is_active 设为 False。

        业务规则：
        - 若该部门下有活跃子部门（is_active=True），则拒绝删除（防止树结构孤立）
        - 若有员工关联到该部门，不阻止删除（员工的 department_id 保留，但部门本身不可见）

        参数：
            db            - 异步数据库会话
            department_id - 目标部门主键 ID

        异常：
            HTTPException(404) - 部门不存在
            HTTPException(400) - 存在活跃子部门，无法删除
        """
        dept = await DepartmentService.get_by_id(db, department_id)
        # 检查是否有活跃子部门（parent_id = 当前部门 ID 且 is_active=True）
        children = await db.execute(
            select(func.count())
            .select_from(Department)
            .where(Department.parent_id == department_id, Department.is_active == True)
        )
        if children.scalar() > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该部门下存在子部门，无法删除",
            )
        dept.is_active = False  # 软删除
        await db.flush()

    @staticmethod
    async def get_tree(
        db: AsyncSession,
        location_id: Optional[int] = None,
    ) -> List[DepartmentTreeNode]:
        """
        获取部门树形结构（嵌套 JSON）。

        实现策略（内存构建，避免递归 SQL）：
            1. 一次查询获取所有活跃部门（可按地点过滤）
            2. 将每个部门转为 DepartmentTreeNode（含空 children 列表）
            3. 建立 id → node 字典映射
            4. 遍历所有节点：有 parent_id 且父节点存在 → 追加到父节点的 children；否则为根节点
            5. 返回根节点列表（每个根节点递归包含其所有子孙节点）

        此方式相比递归 CTE SQL 查询更简单，在部门数量（通常 <1000）的场景下效率足够。

        参数：
            db          - 异步数据库会话
            location_id - 按工作地点 ID 过滤（可选），用于展示特定地点的部门树

        返回：
            List[DepartmentTreeNode]，每个节点含 children 字段（递归嵌套）
            示例结构：
                [
                    {id: 1, name: "技术中心", children: [
                        {id: 3, name: "前端部", children: [...]},
                        {id: 4, name: "后端部", children: [...]},
                    ]},
                    {id: 2, name: "市场部", children: [...]},
                ]
        """
        # 一次性查询所有活跃部门，按排序字段和 ID 排列（确保父节点在子节点前不必要，但顺序一致）
        stmt = (
            select(Department)
            .where(Department.is_active == True)
            .order_by(Department.sort_order, Department.id)
        )
        if location_id is not None:
            stmt = stmt.where(Department.location_id == location_id)

        result = await db.execute(stmt)
        departments = list(result.scalars().all())

        # 构建 id → DepartmentTreeNode 映射（每个节点初始 children 为空列表）
        node_map: dict[int, DepartmentTreeNode] = {}
        for dept in departments:
            node_map[dept.id] = DepartmentTreeNode(
                id=dept.id,
                name=dept.name,
                code=dept.code,
                parent_id=dept.parent_id,
                company_id=dept.company_id,
                location_id=dept.location_id,
                manager_id=dept.manager_id,
                headcount_quota=dept.headcount_quota,
                level=dept.level,
                sort_order=dept.sort_order,
                is_active=dept.is_active,
                children=[],
            )

        # 组装树：有父节点的挂到父节点的 children 下；根节点（parent_id=None 或父节点不在结果集中）收集为根列表
        roots: List[DepartmentTreeNode] = []
        for node in node_map.values():
            if node.parent_id and node.parent_id in node_map:
                # 非根节点：追加到父节点的 children 列表
                node_map[node.parent_id].children.append(node)
            else:
                # 根节点：parent_id 为 None，或父部门已停用不在结果集中
                roots.append(node)

        return roots


class OrgCompanyLayoutService:
    """组织架构中的公司节点布局服务（不影响公司主数据）。"""

    @staticmethod
    async def get_all(db: AsyncSession) -> List[OrgCompanyLayout]:
        result = await db.execute(
            select(OrgCompanyLayout).order_by(OrgCompanyLayout.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def _get_company_map(db: AsyncSession) -> dict[int, Company]:
        result = await db.execute(select(Company))
        return {item.id: item for item in result.scalars().all()}

    @staticmethod
    async def _validate_parent(
        db: AsyncSession,
        company_id: int,
        parent_company_id: Optional[int],
    ) -> None:
        companies = await OrgCompanyLayoutService._get_company_map(db)
        if company_id not in companies:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"公司 ID={company_id} 不存在",
            )
        if parent_company_id is None:
            return
        if parent_company_id not in companies:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"上级公司 ID={parent_company_id} 不存在",
            )
        if parent_company_id == company_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="上级公司不能是自己",
            )

        layout_result = await db.execute(select(OrgCompanyLayout))
        layout_map = {item.company_id: item for item in layout_result.scalars().all()}

        def effective_parent(cid: int, pending: Optional[int] = None) -> Optional[int]:
            if cid == company_id:
                return pending
            item = layout_map.get(cid)
            if item is not None:
                return item.parent_company_id
            company = companies.get(cid)
            return company.parent_company_id if company else None

        visited: set[int] = set()
        cursor = parent_company_id
        while cursor is not None:
            if cursor == company_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="不能将公司设为其子孙公司的下级（检测到循环）",
                )
            if cursor in visited:
                break
            visited.add(cursor)
            cursor = effective_parent(cursor, pending=parent_company_id)

    @staticmethod
    async def upsert(
        db: AsyncSession,
        company_id: int,
        data: OrgCompanyLayoutUpdate,
    ) -> OrgCompanyLayout:
        update_data = data.model_dump(exclude_unset=True)
        if "parent_company_id" in update_data:
            await OrgCompanyLayoutService._validate_parent(
                db, company_id, update_data.get("parent_company_id")
            )
        else:
            # 不传 parent_company_id 时，也要确保 company 存在
            companies = await OrgCompanyLayoutService._get_company_map(db)
            if company_id not in companies:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"公司 ID={company_id} 不存在",
                )

        result = await db.execute(
            select(OrgCompanyLayout).where(OrgCompanyLayout.company_id == company_id)
        )
        item = result.scalar_one_or_none()
        if item is None:
            item = OrgCompanyLayout(
                company_id=company_id,
                parent_company_id=update_data.get("parent_company_id"),
                is_visible=update_data.get("is_visible", True),
            )
            db.add(item)
            await db.flush()
            await db.refresh(item)
            return item

        for field, value in update_data.items():
            setattr(item, field, value)
        await db.flush()
        await db.refresh(item)
        return item

    @staticmethod
    async def hide(db: AsyncSession, company_id: int) -> OrgCompanyLayout:
        item = await OrgCompanyLayoutService.upsert(
            db,
            company_id=company_id,
            data=OrgCompanyLayoutUpdate(is_visible=False),
        )
        return item
