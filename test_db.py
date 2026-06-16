from database import get_db_connection

try:

    conn = get_db_connection()

    print("✅ Database Connected")

    conn.close()

except Exception as ex:

    print("❌ Database Error")
    print(ex)