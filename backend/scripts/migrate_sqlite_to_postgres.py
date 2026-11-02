"""一次性将旧 SQLite 行程迁移到 PostgreSQL，保留 SQLite 快照。"""
import argparse
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings  # 加载开发环境变量
from app.models.postgres_trip_store import PostgresTripStore
from app.models.trip_store import TripStore

TRIP_FIELDS = ('trip_id', 'version', 'state_json', 'plan_json', 'expires_at', 'created_at', 'updated_at')
HISTORY_FIELDS = ('trip_id', 'version', 'skill', 'city', 'title', 'origin_name', 'state_json', 'plan_json', 'updated_at')


def normalized(field, value):
    if field in ('state_json', 'plan_json'):
        return json.loads(value)
    if field in ('expires_at', 'created_at', 'updated_at'):
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value.astimezone(timezone.utc)
    return value


def migrate(source: Path, database_url: str | None = None) -> tuple[Path, int, int]:
    if not source.is_file():
        raise FileNotFoundError(f'SQLite 文件不存在：{source}')
    backup = source.with_name(source.name + '.backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    # sqlite backup API 取得一致快照，原文件保持不变。
    with closing(sqlite3.connect(f'file:{source.resolve()}?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(backup)) as dest:
            src.backup(dest)
    with closing(sqlite3.connect(f'file:{backup.resolve()}?mode=ro', uri=True)) as src:
        src.row_factory = sqlite3.Row
        trips = [dict(row) for row in src.execute('SELECT * FROM trips')]
        has_history = src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trip_history'").fetchone()
        history = [dict(row) for row in src.execute('SELECT * FROM trip_history')] if has_history else []
    seen = {(row['trip_id'], row['version']) for row in history}
    for row in trips:
        if (row['trip_id'], row['version']) not in seen:
            record = TripStore._record(row)
            history.append(dict(trip_id=row['trip_id'], version=row['version'], skill=record.state.current_skill,
                                city=record.state.city, title=record.latest_plan.title,
                                origin_name=record.state.origin_name, state_json=row['state_json'],
                                plan_json=row['plan_json'], updated_at=row['updated_at']))
    # 验证可解析，避免迁移后 API 才发现损坏。
    for row in trips + history:
        TripStore._record(row)
    store = PostgresTripStore(database_url)
    with store._connect() as conn:
        for table, fields, rows, key_fields in (
            ('trips', TRIP_FIELDS, trips, ('trip_id',)),
            ('trip_history', HISTORY_FIELDS, history, ('trip_id', 'version')),
        ):
            where = ' AND '.join(f'{field} = %s' for field in key_fields)
            placeholders = ', '.join(['%s'] * len(fields))
            for row in rows:
                existing = conn.execute(f'SELECT * FROM {table} WHERE {where}',
                                        tuple(row[field] for field in key_fields)).fetchone()
                if existing is not None:
                    if any(normalized(field, existing[field]) != normalized(field, row[field]) for field in fields):
                        raise ValueError(f'PostgreSQL 中已有不同的 {table} 记录，已取消迁移：{row["trip_id"]}')
                    continue
                conn.execute(f'INSERT INTO {table} ({", ".join(fields)}) VALUES ({placeholders})',
                             tuple(row[field] for field in fields))
    return backup, len(trips), len(history)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path,
                        default=Path(__file__).resolve().parents[1] / '.runtime' / 'trips.sqlite3')
    args = parser.parse_args()
    backup, trips, history = migrate(args.source)
    print(f'迁移完成：{trips} 条行程，{history} 条历史版本。')
    print(f'SQLite 备份：{backup}')


if __name__ == '__main__':
    main()
