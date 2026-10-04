import uuid

def generate_item(concept_id: str, topic_id: str, custom_stem: str = None) -> dict:
    """
    Mock LLM generation step retrieving from concepts.api.
    Generates a structured assessment item adhering to core/models.
    """
    item_id = f"item_{uuid.uuid4().hex[:8]}"
    return {
        "item_id": item_id,
        "concept_id": concept_id,
        "topic_id": topic_id,
        "type": "mcq",
        "bloom": "apply",
        "stem": custom_stem or f"Assess concept {concept_id}",
        "options_json": '["A", "B", "C", "D"]',
        "answer_json": '["A"]',
        "rubric_json": '{"explanation": "A is correct"}',
        "unit_ids_json": '["u_123"]', # Linked via tutor.cite.resolve
        "difficulty_b": 0.0,
        "discrimination_a": 1.0
    }
