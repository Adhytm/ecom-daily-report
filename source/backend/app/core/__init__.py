"""core 包：核心业务引擎（解析、归一化、映射、聚合、预警）。

本包同时承载统一业务异常 AppError，供 API 层转换为统一错误响应。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """业务异常基类。

    API 层的全局异常处理器会把它转换为统一响应包裹：
    ``{"ok": false, "error": {"code": ..., "message": ..., "detail": ...}}``。
    """

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 500,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail


# ---- 具体业务异常（错误码见规划第 7 节枚举） ----


class FileParseError(AppError):
    """文件物理解析失败（编码、表头定位等）。"""

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__("FILE_PARSE_ERROR", message, status_code=400, detail=detail)


class EncodingError(FileParseError):
    """文件编码无法识别。"""

    def __init__(self, message: str, detail: Any = None) -> None:
        # 绕过 FileParseError 的 code 绑定，使用独立的 ENCODING_ERROR 错误码
        AppError.__init__(self, "ENCODING_ERROR", message, status_code=400, detail=detail)


class AdapterConfigError(AppError):
    """适配器 YAML 校验失败。"""

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__("ADAPTER_CONFIG_INVALID", message, status_code=422, detail=detail)


class AdapterNotFoundError(AppError):
    """平台适配器不存在。"""

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__("ADAPTER_NOT_FOUND", message, status_code=404, detail=detail)


class MissingRequiredColumnError(AppError):
    """归一化时必需列缺失。"""

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__("MISSING_REQUIRED_COLUMN", message, status_code=400, detail=detail)


class SkuNotFoundError(AppError):
    """内部 SKU 不存在。"""

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__("SKU_NOT_FOUND", message, status_code=404, detail=detail)


class SkuInUseError(AppError):
    """SKU 仍被销售明细引用，删除被拒绝（HTTP 409）。"""

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__("SKU_IN_USE", message, status_code=409, detail=detail)


class ReportNotFoundError(AppError):
    """日报不存在。"""

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__("REPORT_NOT_FOUND", message, status_code=404, detail=detail)


class NoDataForDateError(AppError):
    """指定日期无任何销售数据。"""

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__("NO_DATA_FOR_DATE", message, status_code=404, detail=detail)


class ValidationError(AppError):
    """业务参数校验失败。"""

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__("VALIDATION_ERROR", message, status_code=422, detail=detail)


class UploadNotFoundError(AppError):
    """上传记录不存在。"""

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__("UPLOAD_NOT_FOUND", message, status_code=404, detail=detail)


class DuplicateUploadCommittedError(AppError):
    """同一文件内容（sha256）已有 committed 记录，重复提交被拒绝（HTTP 409）。

    前端可用 ``force=true`` 显式覆盖（用户确认"我就是要重复入库"）。
    """

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__("DUPLICATE_UPLOAD_COMMITTED", message, status_code=409, detail=detail)
