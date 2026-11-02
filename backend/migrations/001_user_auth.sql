-- 用户与会话。旧行程保持未归属，后续由管理员显式迁移。
BEGIN;
CREATE TABLE IF NOT EXISTS users (
 id UUID PRIMARY KEY, username VARCHAR(32) NOT NULL UNIQUE,
 password_hash TEXT NOT NULL, is_active BOOLEAN NOT NULL DEFAULT TRUE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS user_sessions (
 id UUID PRIMARY KEY, user_id UUID NOT NULL REFERENCES users(id),
 token_hash TEXT NOT NULL UNIQUE, expires_at TIMESTAMPTZ NOT NULL,
 revoked_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_sessions_expires_at ON user_sessions(expires_at);
ALTER TABLE trips ADD COLUMN IF NOT EXISTS user_id UUID;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='trips'::regclass AND conname='trips_user_id_fkey') THEN
  ALTER TABLE trips ADD CONSTRAINT trips_user_id_fkey FOREIGN KEY(user_id) REFERENCES users(id);
 END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_trips_user_updated ON trips(user_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS auth_rate_limits (
 bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_rate_limits_expires ON auth_rate_limits(expires_at);
COMMIT;
