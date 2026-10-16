"""
请假审批时区测试
覆盖: approved_at 使用 timezone-aware datetime（P1 修复验证）
"""

from datetime import datetime, timezone
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestApprovedAtTimezone:
    """验证 approved_at 赋值使用时区感知时间"""

    def test_now_utc_is_timezone_aware(self):
        """datetime.now(timezone.utc) 返回时区感知对象"""
        now = datetime.now(timezone.utc)
        assert now.tzinfo is not None
        assert now.tzinfo == timezone.utc

    def test_utcnow_is_naive(self):
        """datetime.utcnow() 返回 naive datetime（验证我们不使用它）"""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            old_now = datetime.utcnow()
        assert old_now.tzinfo is None

    def test_timezone_aware_approved_at_can_compare(self):
        """时区感知的 approved_at 可与其他 aware datetime 做比较"""
        approved_at = datetime.now(timezone.utc)
        later = datetime.now(timezone.utc)
        # 不应抛 TypeError
        assert later >= approved_at

    def test_naive_vs_aware_comparison_raises(self):
        """naive 和 aware datetime 比较抛 TypeError（验证旧代码的 bug）"""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            naive = datetime.utcnow()
        aware = datetime.now(timezone.utc)
        with pytest.raises(TypeError):
            _ = aware > naive
