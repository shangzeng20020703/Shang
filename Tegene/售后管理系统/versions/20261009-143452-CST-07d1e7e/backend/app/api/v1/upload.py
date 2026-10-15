"""
文件上传接口 - File Upload API
================================

本模块提供售后管理系统的通用文件上传功能，支持：

**支持的文件类型**：
- PDF 文档（`application/pdf`，.pdf）
- 图片（`image/jpeg`/`image/png`/`image/gif`/`image/webp` 等）
- Word / Excel / PowerPoint 文档（`.doc`/`.docx`/`.xls`/`.xlsx`/`.ppt`/`.pptx`）
- TXT / CSV 等轻量文本材料
- 音视频材料（移动端聊天语音、视频消息）

**典型使用场景**：
- 员工头像上传（category = "avatar"）
- 劳动合同附件（category = "contract"）
- 培训材料附件（category = "training"）
- 员工档案材料（category = "document"）
- 其他通用文件（category = "general"，默认）

**存储策略**：
- 上传目录：项目根目录的 `uploads/{category}/`（相对于 backend/ 的父级目录）
- 文件名：UUID hex 随机重命名防止冲突和路径遍历攻击
- 访问方式：Nginx 静态文件服务，访问路径为 `/uploads/{category}/{filename}`

**文件限制**：
- 最大文件大小：20MB
- 允许的扩展名：.pdf / .jpg / .jpeg / .png / .gif / .webp / .doc / .docx /
  .xls / .xlsx / .ppt / .pptx / .txt / .csv / .mp4 / .mp3 等

**安全措施**：
- 扩展名白名单校验（防止上传可执行文件）
- 文件大小限制（防止存储耗尽）
- UUID 重命名（防止文件名冲突和目录遍历）
- 登录认证（`get_current_user`），防止未授权上传

**路由前缀**（注册在 router.py）：`/api/v1/upload`

**技术栈**：FastAPI UploadFile + Python pathlib + uuid

**注意**：
- 本接口未记录上传历史到数据库，文件管理（删除/清理）需手动或通过 cron 任务处理
- 如需将文件关联到具体业务实体（如合同），需在对应业务接口中保存返回的 URL
"""
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import JSONResponse

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.employee import Employee
from sqlalchemy.ext.asyncio import AsyncSession

# 路由器实例，由 api/v1/router.py 以 prefix="/upload" 挂载
router = APIRouter()

# 上传根目录：backend/ 的父目录下的 uploads/ 文件夹
# __file__ = .../backend/app/api/v1/upload.py
# .parent.parent.parent.parent = 项目根目录（aftersales-field-management/backend/）的父级
# 即最终路径为 aftersales-field-management/uploads/（与 backend/ 和 frontend/ 同级）
UPLOAD_DIR = Path(__file__).parent.parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 允许上传的 MIME 类型白名单
ALLOWED_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/heic",
    "image/heif",
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "audio/mpeg",
    "audio/mp4",
    "audio/wav",
    "audio/webm",
    "audio/x-m4a",
    "application/msword",                                                          # .doc
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",    # .docx
    "application/vnd.ms-excel",                                                    # .xls
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",           # .xlsx
    "application/vnd.ms-powerpoint",                                               # .ppt
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",   # .pptx
    "text/plain",
    "text/csv",
}

# 允许上传的文件扩展名白名单（小写）
ALLOWED_EXTENSIONS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".heic",
    ".heif",
    ".mp4",
    ".mov",
    ".webm",
    ".mp3",
    ".m4a",
    ".wav",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".txt",
    ".csv",
}
SAFE_CATEGORY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,50}$")

# 单次上传最大文件大小（MB）
MAX_SIZE_MB = 20


@router.post("", summary="上传文件（合同附件、档案材料等）")
async def upload_file(
    file: UploadFile = File(...),
    category: str = "general",
    current_user: Employee = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    POST /upload

    通用文件上传接口。接受 multipart/form-data 格式的文件数据，
    校验通过后保存到服务器磁盘，并返回可供前端直接访问的静态 URL。

    **请求参数**（multipart/form-data）：
    - `file`（必填）：上传的文件，支持类型：PDF / 图片 / Office 文档 / TXT / CSV
    - `category`（可选，Query 参数）：文件分类，决定存储子目录，可选值：
      - `contract`：劳动合同附件（存入 uploads/contract/）
      - `document`：员工档案材料（存入 uploads/document/）
      - `avatar`：员工头像（存入 uploads/avatar/）
      - `training`：培训材料（存入 uploads/training/）
      - `general`（默认）：其他通用文件（存入 uploads/general/）

    **校验规则**：
    1. 扩展名必须在白名单中（PDF、图片、Office 文档、TXT、CSV），否则返回 400
    2. 文件大小不超过 20MB，否则返回 400

    **响应**（JSON，200 OK）：
    ```json
    {
      "url": "/uploads/contract/a3f8b2c1d4e5f6a7b8c9d0e1f2a3b4c5.pdf",
      "filename": "劳动合同-张三.pdf",
      "size": 204800
    }
    ```
    - `url`：文件的静态访问路径（由 Nginx 或 FastAPI StaticFiles 提供服务）
    - `filename`：原始文件名（上传时的文件名，用于前端展示）
    - `size`：文件大小（字节数）

    **错误响应**：
    - 400：文件类型不支持 或 文件大小超出限制
    - 401：未登录（JWT Token 无效或过期）

    **权限**：登录用户（`get_current_user`）

    **存储细节**：
    - 文件保存路径：`{UPLOAD_DIR}/{category}/{uuid_hex}{suffix}`
    - UUID 重命名：防止文件名冲突和路径遍历攻击
    - 上传目录按 category 自动创建（parents=True, exist_ok=True）

    **注意事项**：
    - 本接口不记录上传历史到数据库，返回的 URL 需由调用方保存到对应业务记录
    - `db` 参数在当前实现中未使用，预留用于未来扩展（如记录上传日志）
    """
    if not SAFE_CATEGORY_PATTERN.fullmatch(category):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="上传分类仅支持字母、数字、下划线和连字符",
        )

    # 校验扩展名（取小写，统一处理 .JPG/.Jpg 等大小写变体）
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的文件类型 {suffix}，仅支持 PDF/JPG/PNG/GIF/WEBP/HEIC/MP4/MOV/WEBM/MP3/M4A/WAV/DOC/DOCX/XLS/XLSX/PPT/PPTX/TXT/CSV",
        )

    # 读取全部内容到内存后校验大小
    # 注意：大文件会占用较多内存，当前 20MB 限制下影响可接受
    content = await file.read(MAX_SIZE_MB * 1024 * 1024 + 1)
    if len(content) > MAX_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"文件大小超过限制（最大 {MAX_SIZE_MB}MB）",
        )

    # 按 category 创建子目录（如 uploads/contract/），不存在则自动创建
    category_dir = UPLOAD_DIR / category
    category_dir.mkdir(parents=True, exist_ok=True)

    # UUID hex（32位无连字符的十六进制字符串）+ 原始扩展名 = 唯一文件名
    new_filename = f"{uuid.uuid4().hex}{suffix}"
    dest = category_dir / new_filename
    dest.write_bytes(content)

    # 返回相对路径 URL（Nginx 配置 location /uploads/ 指向 UPLOAD_DIR）
    url = f"/uploads/{category}/{new_filename}"
    from app.models.field_service import FieldFile
    db.add(FieldFile(path=url, owner_id=current_user.id))
    await db.flush()
    return JSONResponse({"url": url, "filename": file.filename, "size": len(content)})
