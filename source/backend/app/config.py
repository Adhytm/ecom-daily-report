"""全局配置（pydantic-settings）。

所有环境变量均可覆盖，约定（规划 2.3 / T01）：
- ECOM_DB_PATH       SQLite 数据库文件路径（默认 <项目根>/data/app.db）
- COMPANY_NAME       日报与摘要中的公司名（默认 "XX"）
- SUMMARY_USE_WAN    摘要中金额 >= 1 万时是否使用万元简写（默认 true）
- EXPORT_DIR         导出文件目录（默认 <项目根>/data/exports）
- CORS_ORIGINS       允许的跨域来源，JSON 数组格式（开发模式含 http://localhost:5173）
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：backend/app/config.py 向上两级
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # 应用版本（/api/health 返回）
    app_version: str = "0.1.0"

    # 数据库与导出目录
    db_path: Path = Field(
        default=PROJECT_ROOT / "data" / "app.db",
        validation_alias="ECOM_DB_PATH",
    )
    export_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "exports",
        validation_alias="EXPORT_DIR",
    )
    incoming_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "incoming",
        validation_alias="INCOMING_DIR",
    )

    # 日报文案
    company_name: str = Field(default="XX", validation_alias="COMPANY_NAME")
    summary_use_wan: bool = Field(default=True, validation_alias="SUMMARY_USE_WAN")

    # 跨域（开发模式允许 Vite dev server）
    cors_origins: list[str] = Field(
        default=["http://localhost:5173"], validation_alias="CORS_ORIGINS"
    )

    # 上传限制（规划 8.2：单文件 <= 50MB）
    max_upload_mb: int = 50

    # 内置适配器 YAML 目录
    adapters_dir: Path = PROJECT_ROOT / "backend" / "app" / "adapters"


settings = Settings()
