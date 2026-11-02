"""密码哈希与校验。调用方在线程池执行，禁止记录传入密码。"""
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError

_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
# 缺失账号也执行哈希校验，避免直接跳过昂贵计算。
_dummy_hash = _hasher.hash("dummy-account-password-not-used-for-login")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        valid = _hasher.verify(password_hash or _dummy_hash, password)
        return bool(password_hash) and valid
    except (VerificationError, InvalidHashError):
        return False
