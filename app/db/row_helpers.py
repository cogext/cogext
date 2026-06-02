def row_to_dict(row: dict) -> dict:
    """Pass-through for supabase-py REST response rows.

    supabase-py already returns Python-native types: dicts for JSONB,
    strings for UUIDs and timestamps. Pydantic handles the rest.
    """
    return dict(row)
