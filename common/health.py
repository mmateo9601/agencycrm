from django.db import DatabaseError, connection


def database_available() -> bool:
    """Probe the configured database without returning operational details."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() == (1,)
    except DatabaseError:
        return False
