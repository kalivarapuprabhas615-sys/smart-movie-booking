"""Utility commands:  python manage.py reseed | oracle-seed | test"""
import os, sys, subprocess
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(ROOT, "backend"))
cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd == "reseed":
    import db
    path = os.environ.get("DATABASE_PATH", os.path.join(ROOT, "data", "smartmovie.db")); db.init_db(path).close(); print("database rebuilt:", path)
elif cmd == "oracle-seed":
    sys.path.insert(0, os.path.join(ROOT, "database")); import seed_data
    seed_data.write_oracle_sql(seed_data.build_dataset(), os.path.join(ROOT, "database", "oracle", "03_seed.sql")); print("wrote database/oracle/03_seed.sql")
elif cmd == "test":
    sys.exit(subprocess.call([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT))
else:
    print(__doc__)
