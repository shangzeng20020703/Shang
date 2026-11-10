from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.approval import _attachment_display_text
from app.services.approval import validate_form_payload


@pytest.mark.asyncio
async def test_attachment_field_accepts_uploaded_file_metadata_and_rejects_placeholders():
    fields = [
        SimpleNamespace(
            code="receipt",
            field_type="attachment",
            is_required=True,
            options_json=None,
        )
    ]

    await validate_form_payload(
        fields,
        {
            "receipt": [
                {
                    "name": "交通票据.pdf",
                    "url": "/uploads/approval/receipt.pdf",
                    "size": 2048,
                }
            ]
        },
    )

    with pytest.raises(HTTPException):
        await validate_form_payload(fields, {"receipt": ["附件1"]})

    with pytest.raises(HTTPException):
        await validate_form_payload(fields, {"receipt": []})


def test_attachment_display_text_uses_original_file_names():
    value = [
        {"name": "报销单.pdf", "url": "/uploads/approval/a.pdf"},
        {"filename": "交通发票.png", "url": "/uploads/approval/b.png"},
    ]

    assert _attachment_display_text(value) == "报销单.pdf、交通发票.png"
