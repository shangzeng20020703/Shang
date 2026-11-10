from app.api.v1 import upload


def test_upload_whitelist_covers_employee_attachment_file_types():
    required_extensions = {
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".mp4",
        ".mov",
        ".webm",
        ".mp3",
        ".m4a",
        ".wav",
        ".txt",
        ".csv",
    }
    assert required_extensions.issubset(upload.ALLOWED_EXTENSIONS)
    required_types = {
        "image/webp",
        "video/mp4",
        "video/quicktime",
        "video/webm",
        "audio/mpeg",
        "audio/mp4",
        "audio/wav",
        "audio/webm",
        "audio/x-m4a",
    }
    assert required_types.issubset(upload.ALLOWED_TYPES)
