import os

import duckdb


def main() -> int:
    db = r"d:\University\Semester 8th\FYP\AI\data\processed\cricket.duckdb"
    print("db_exists", os.path.exists(db))
    if not os.path.exists(db):
        return 1

    con = duckdb.connect(db, read_only=True)
    try:
        rows = con.execute("select count(1) from deliveries").fetchone()[0]
        non_null_bowler_hand = con.execute(
            "select count(1) from deliveries where bowler_hand is not null and lower(bowler_hand) in ('left','right')"
        ).fetchone()[0]
        non_null_batter_hand = con.execute(
            "select count(1) from deliveries where batter_hand is not null and lower(batter_hand) in ('left','right')"
        ).fetchone()[0]

        print("rows", rows)
        print("non_null_bowler_hand", non_null_bowler_hand)
        print("non_null_batter_hand", non_null_batter_hand)
    finally:
        con.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

