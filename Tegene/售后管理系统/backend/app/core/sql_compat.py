"""
数据库方言兼容辅助工具。
"""

from sqlalchemy import case


def order_by_nulls_last_desc(column):
    """
    跨数据库实现 DESC NULLS LAST。

    PostgreSQL 原生支持 ``DESC NULLS LAST``，MySQL 不支持；
    这里用 CASE 排序保证两端表现一致。
    """
    return (
        case((column.is_(None), 1), else_=0),
        column.desc(),
    )
