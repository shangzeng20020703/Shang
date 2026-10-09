"""
模块名称: schemas/organization.py
模块作用: 组织架构域（Organization Domain）的所有 Pydantic v2 数据传输对象（DTO）定义

功能覆盖:
  - 工作地点管理: LocationBase / LocationCreate / LocationUpdate / LocationOut
  - 部门管理: DepartmentBase / DepartmentCreate / DepartmentUpdate / DepartmentOut
  - 部门树结构: DepartmentTreeNode（递归嵌套）

依赖关系:
  - 无其他业务 Schema 依赖
  - LocationOut 被 DepartmentOut 内嵌使用（展示部门所属地点详情）
  - 被 app/api/v1/endpoints/organization.py 所有路由使用
  - 被 app/schemas/employee.py 间接引用（employee.location_id 对应 Location）

数据流向:
  HTTP Request Body → *Create/*Update (Pydantic 验证) → Service Layer (ORM)
  ORM Model → *Out (model_config from_attributes=True) → HTTP Response JSON

路由关键点（重要）:
  - 组织架构模块没有 /organizations/ 根路由
  - 工作地点路由: GET/POST /api/v1/organizations/locations
  - 部门路由: GET /api/v1/departments（返回部门树）
  - DepartmentTreeNode 使用递归结构，model_rebuild() 调用在文件末尾

业务说明:
  - Location（工作地点）: 描述公司的物理办公地点，如"北京总部"、"东莞工厂"
    每个员工归属一个地点，社保/公积金城市与地点相关
  - Department（部门）: 树状结构，通过 parent_id 自引用实现任意层级
    level 字段表示深度（1=顶级部门），code 字段由系统自动生成4位编码（若不填）
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ==================== Location ====================

class LocationBase(BaseModel):
    """
    用途: 工作地点写操作 Schema 的基类
    业务: 描述公司的一个物理办公/生产地点
    字段说明:
      - name: 地点名称，如"北京总部"、"东莞工厂"、"上海研发中心"
      - city: 所在城市，与员工社保/公积金缴纳城市关联
      - contact_phone: 该地点的总机或前台电话
      - is_active: False 时前端下拉不显示该地点，但历史数据保留
    """
    name: str = Field(..., max_length=100, description="地点名称")
    address: Optional[str] = Field(None, max_length=500, description="详细地址")
    city: Optional[str] = Field(None, max_length=50, description="所在城市")
    contact_phone: Optional[str] = Field(None, max_length=20, description="联系电话")
    is_active: bool = Field(True, description="是否启用")


class LocationCreate(LocationBase):
    """
    用途: POST /api/v1/organizations/locations 新建工作地点的请求体
    业务: 直接继承 LocationBase，无额外字段
    """
    pass


class LocationUpdate(BaseModel):
    """
    用途: PATCH /api/v1/organizations/locations/{id} 更新工作地点的请求体
    业务: 所有字段均为可选，只传需要修改的字段（部分更新语义）
    常见场景: 更新地址、停用地点（is_active=False）
    """
    name: Optional[str] = Field(None, max_length=100)
    address: Optional[str] = Field(None, max_length=500)
    city: Optional[str] = Field(None, max_length=50)
    contact_phone: Optional[str] = Field(None, max_length=20)
    is_active: Optional[bool] = None


class LocationOut(LocationBase):
    """
    用途: 工作地点详情/列表接口的响应体，从 ORM Location 模型序列化
    业务: 继承 LocationBase 的所有字段，增加数据库自增 id
    model_config from_attributes=True: 允许从 SQLAlchemy ORM 实例直接构造
    注意: 此类被 DepartmentOut 内嵌使用，作为部门所属地点的详情展示
    """
    id: int   # 数据库自增主键，地点唯一标识

    model_config = {"from_attributes": True}


# ==================== Department ====================

class DepartmentBase(BaseModel):
    """
    用途: 部门写操作 Schema 的基类
    业务: 描述组织树中的一个部门节点
    树结构说明:
      - parent_id=None 表示顶级部门（一级部门）
      - level 字段表示节点深度，1=顶级，2=二级，依此类推
      - children 关系在 DepartmentTreeNode 中以递归形式展开
    字段说明:
      - code: 部门编码，不填则自动生成4位随机编码（如"0042"）
      - manager_id: 部门负责人的员工 ID（外键到 Employee 表）
      - sort_order: 同级部门间的排序，数字小的排在前面
      - is_active: False 时该部门在前端下拉中不显示，但已归属员工仍保留关联
    """
    name: str = Field(..., max_length=100, description="部门名称")
    code: Optional[str] = Field(None, max_length=50, description="部门编码（不填则自动生成4位编码）")
    parent_id: Optional[int] = Field(None, description="上级部门ID")
    company_id: Optional[int] = Field(None, description="所属公司ID")
    location_id: Optional[int] = Field(None, description="所属工作地点ID")
    manager_id: Optional[int] = Field(None, description="部门负责人ID")
    headcount_quota: int = Field(0, ge=0, description="部门编制人数")
    level: int = Field(1, ge=1, description="层级深度")
    sort_order: int = Field(0, description="排序序号")
    is_active: bool = Field(True, description="是否启用")


class DepartmentCreate(DepartmentBase):
    """
    用途: POST /api/v1/departments 新建部门的请求体
    业务: 直接继承 DepartmentBase，无额外字段
    注意: code 不填时 Service 层自动生成4位编码
    """
    pass


class DepartmentUpdate(BaseModel):
    """
    用途: PATCH /api/v1/departments/{id} 更新部门信息的请求体
    业务: 所有字段均为可选，只传需要修改的字段（部分更新语义）
    常见场景:
      - 修改部门名称（name）
      - 调整上级部门（parent_id），即部门搬迁到其他父节点
      - 更换部门负责人（manager_id）
      - 停用部门（is_active=False）
    """
    name: Optional[str] = Field(None, max_length=100)
    code: Optional[str] = Field(None, max_length=50)
    parent_id: Optional[int] = None           # 修改上级部门（调整树结构）
    company_id: Optional[int] = None
    location_id: Optional[int] = None
    manager_id: Optional[int] = None          # 更换部门负责人
    headcount_quota: Optional[int] = Field(None, ge=0)
    level: Optional[int] = Field(None, ge=1)
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class DepartmentOut(DepartmentBase):
    """
    用途: GET /api/v1/departments/{id} 部门详情接口的响应体（不含子部门树）
    业务: 继承 DepartmentBase 所有字段，增加 id、时间戳和关联地点详情
    特殊字段:
      - location: 内嵌的 LocationOut 对象，展示该部门所属地点的完整信息
        若 location_id 为 None，则 location 也为 None
    注意: 此类不含 children 字段，不展示子部门。
          需要完整树结构时请使用 DepartmentTreeNode
    """
    id: int
    created_at: datetime
    updated_at: datetime
    location: Optional[LocationOut] = None    # 关联的工作地点详情（内嵌展示）

    model_config = {"from_attributes": True}


class DepartmentTreeNode(BaseModel):
    """
    用途: GET /api/v1/departments 部门树接口的响应体（递归嵌套结构）
    业务: 单个节点包含本部门基础信息 + children（子部门列表）
          children 中的每个元素也是 DepartmentTreeNode，支持任意层级
    使用场景:
      - 前端左侧部门树菜单渲染
      - 员工归属部门下拉选择（需要展示层级关系）
      - 组织架构图展示
    注意:
      - 此类在文件末尾调用 model_rebuild() 以支持 Pydantic v2 的前向引用
      - children 默认为空列表，叶子节点的 children=[]
      - 不含 created_at/updated_at 和 location 详情，轻量化设计
    """
    id: int
    name: str
    code: Optional[str] = None
    parent_id: Optional[int] = None            # None 表示顶级部门
    company_id: Optional[int] = None
    location_id: Optional[int] = None
    manager_id: Optional[int] = None
    headcount_quota: int
    level: int                                 # 树的深度，1=根节点
    sort_order: int
    is_active: bool
    children: List["DepartmentTreeNode"] = []  # 子节点列表（递归嵌套）

    model_config = {"from_attributes": True}


# 允许递归引用：Pydantic v2 要求在定义包含前向引用的模型后调用 model_rebuild()
# 否则 children 字段的类型解析会失败
DepartmentTreeNode.model_rebuild()


# ==================== Org Company Layout ====================

class OrgCompanyLayoutBase(BaseModel):
    company_id: int = Field(..., description="公司ID")
    parent_company_id: Optional[int] = Field(None, description="组织架构中的上级公司ID")
    is_visible: bool = Field(True, description="是否在组织架构中显示")


class OrgCompanyLayoutUpdate(BaseModel):
    parent_company_id: Optional[int] = Field(None, description="组织架构中的上级公司ID（可置空）")
    is_visible: Optional[bool] = Field(None, description="是否在组织架构中显示")


class OrgCompanyLayoutOut(OrgCompanyLayoutBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
