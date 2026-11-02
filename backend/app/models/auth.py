"""认证输入输出；安全字段不进入用户公开模型。"""
from datetime import datetime
from uuid import UUID
from typing import Literal
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: SecretStr

    @field_validator("username")
    @classmethod
    def username_valid(cls, value):
        import re
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9_]{3,32}", value):
            raise ValueError("用户名应为3至32位字母、数字或下划线")
        return value

    @field_validator("password")
    @classmethod
    def password_valid(cls, value):
        password = value.get_secret_value()
        if not 12 <= len(password) <= 128 or len(password.encode("utf-8")) > 512:
            raise ValueError("密码应为12至128个字符且不超过512字节")
        return value


class PublicUser(BaseModel):
    id: UUID
    username: str
    created_at: datetime


class AuthMeta(BaseModel):
    schema_version: Literal["1.0"] = "1.0"


class LoginData(BaseModel):
    user: PublicUser
    expires_at: datetime
    csrf_token: str


class UserResponse(BaseModel):
    code: Literal[0] = 0
    message: Literal["success"] = "success"
    data: PublicUser
    meta: AuthMeta = AuthMeta()


class LoginResponse(UserResponse):
    data: LoginData


class CsrfData(BaseModel):
    csrf_token: str


class CsrfResponse(UserResponse):
    data: CsrfData


class LogoutResponse(UserResponse):
    data: None = None
