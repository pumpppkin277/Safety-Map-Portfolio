import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["APP_ENV"] = "test"
os.environ.pop("ADMIN_TOKEN", None)
