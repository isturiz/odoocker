#!/usr/bin/env python3
"""
Wait for PostgreSQL server to be available before proceeding.
"""

import argparse
import psycopg2
import sys
import time

if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser(
        description="Wait for PostgreSQL to be available."
    )
    arg_parser.add_argument('--db_host', required=True, help="Database host")
    arg_parser.add_argument('--db_port', required=True, help="Database port")
    arg_parser.add_argument('--db_user', required=True, help="Database user")
    arg_parser.add_argument('--db_password', required=True,
                            help="Database password")
    arg_parser.add_argument('--timeout', type=int, default=5,
                            help="Timeout in seconds")

    args = arg_parser.parse_args()

    start_time = time.time()

    error = ''
    while (time.time() - start_time) < args.timeout:
        try:
            conn = psycopg2.connect(
                user=args.db_user,
                host=args.db_host,
                port=args.db_port,
                password=args.db_password,
                dbname='postgres'
            )
            break
        except psycopg2.OperationalError as e:
            error = e
            print(f"Waiting for PostgreSQL... ({e})", file=sys.stderr)
        else:
            conn.close()
        time.sleep(1)

    if error:
        print(f"Database connection failure: {error}", file=sys.stderr)
        sys.exit(1)
