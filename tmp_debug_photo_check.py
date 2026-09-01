import os
from pathlib import Path
from sqlalchemy import create_engine, text

root = Path(__file__).resolve().parent
env_path = root / '.env'
config = {}
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line:
            key, val = line.split('=', 1)
            val = val.strip().strip('"').strip("'")
            config[key.strip()] = val
print('env DB_URL:', config.get('DB_URL'))
url = config.get('DB_URL')
if not url:
    raise SystemExit('no DB_URL')
url = url.replace('+aiomysql', '+pymysql')
engine = create_engine(url)
with engine.connect() as conn:
    users = conn.execute(text('SELECT id, username, email, photo_path, photo_data IS NOT NULL AS has_photo_data FROM `user` LIMIT 20')).fetchall()
    print('user sample count:', len(users))
    for u in users:
        print(u)
    count = conn.execute(text('SELECT COUNT(*) FROM `user`')).scalar_one()
    print('total users:', count)
static_users = root / 'app' / 'static' / 'users'
print('static_users exists:', static_users.exists())
if static_users.exists():
    files = [p.name for p in static_users.iterdir() if p.is_file()]
    print('static_users files count:', len(files))
    print('static_users files sample:', files[:30])
