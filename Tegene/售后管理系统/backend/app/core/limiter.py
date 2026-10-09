"""
=============================================================================
模块：Core / Rate Limiter
文件：app/core/limiter.py
=============================================================================
作用：
    API 访问频率限制器（限流）。基于 slowapi 库封装，
    防止暴力破解、DDoS 攻击和接口滥用。

功能：
    - 提供全局 limiter 单例，供 FastAPI 装饰器使用
    - 基于客户端 IP 地址进行限流统计
    - 当前主要用于登录接口（5次/分钟）防暴力破解

限流配置示例：
    @limiter.limit("5/minute")     # 每个 IP 每分钟最多 5 次
    @limiter.limit("100/hour")     # 每个 IP 每小时最多 100 次
    @limiter.limit("1000/day")     # 每个 IP 每天最多 1000 次

调用关系：
    被引用方：
        app/main.py            → 挂载到 app.state.limiter
        app/api/v1/auth.py     → @limiter.limit("5/minute") 登录限流

    key_func = get_remote_address：
        使用请求的真实客户端 IP 作为限流 key。
        如果应用部署在反向代理（Nginx）后面，需确保 Nginx 正确设置
        X-Forwarded-For 或 X-Real-IP 请求头。

依赖库：
    slowapi >= 0.1.9  → FastAPI/Starlette 的限流中间件
    限流状态存储：
        默认使用内存存储（重启后重置）。
        如需持久化限流状态，可配置 Redis 后端：
        Limiter(key_func=get_remote_address, storage_uri="redis://localhost:6379")

注意：
    main.py 中还需注册 RateLimitExceeded 异常处理器：
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    否则限流触发时将返回 500 而非 429。
=============================================================================
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

# 全局限流器单例
# key_func=get_remote_address：以客户端 IP 作为限流粒度
# 如需用用户身份限流，可改为自定义 key_func（如从 JWT 提取 user_id）
limiter = Limiter(key_func=get_remote_address)
