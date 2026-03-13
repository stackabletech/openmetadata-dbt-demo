import glob
import os
import sys
import warnings

if not sys.warnoptions:
    warnings.simplefilter("ignore")

import trino

TRINO_HOST = os.environ.get("TRINO_HOST", "trino-coordinator")
TRINO_PORT = int(os.environ.get("TRINO_PORT", "8443"))
SQL_DIR = os.environ.get("SQL_DIR", "/sql")
CREDENTIALS_DIR = "/credentials"


def get_connection():
    kwargs = dict(
        host=TRINO_HOST,
        port=TRINO_PORT,
        user="admin",
        http_scheme="https",
    )

    username_file = os.path.join(CREDENTIALS_DIR, "username")
    password_file = os.path.join(CREDENTIALS_DIR, "password")
    if os.path.isfile(username_file) and os.path.isfile(password_file):
        username = open(username_file).read().strip()
        password = open(password_file).read().strip()
        kwargs["user"] = username
        kwargs["auth"] = trino.auth.BasicAuthentication(username, password)

    conn = trino.dbapi.connect(**kwargs)
    conn._http_session.verify = False
    return conn


def run_query(conn, query):
    cursor = conn.cursor()
    cursor.execute(query)
    return cursor.fetchall()


def main():
    conn = get_connection()
    sql_files = sorted(glob.glob(os.path.join(SQL_DIR, "*.sql")))

    if not sql_files:
        print(f"No .sql files found in {SQL_DIR}")
        sys.exit(1)

    for sql_file in sql_files:
        print(f"=== Executing {sql_file} ===")
        sql = open(sql_file).read().strip()
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement:
                print(f"  > {statement[:80]}{'...' if len(statement) > 80 else ''}")
                result = run_query(conn, statement)
                if result:
                    print(f"  Result: {result}")
        print(f"=== Done: {sql_file} ===")

    print("All SQL files executed successfully.")


if __name__ == "__main__":
    main()
