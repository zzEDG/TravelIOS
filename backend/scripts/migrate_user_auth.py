"""初始化认证表和可空行程归属；不会为旧行程分配用户。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings
from app.models.postgres_trip_store import PostgresTripStore


def migrate(database_url=None):
    store = PostgresTripStore(database_url)
    migration = Path(__file__).resolve().parents[1] / "migrations" / "001_user_auth.sql"
    with store._connect() as conn:
        conn.execute(migration.read_text(encoding="utf-8"))


if __name__ == "__main__":
    migrate()
    print("认证表和行程归属列已就绪；旧行程保持未归属。")
