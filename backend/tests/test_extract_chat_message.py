from app.services.llm_service import extract_chat_message_text


def test_extract_plain_content():
    assert (
        extract_chat_message_text(
            {"choices": [{"message": {"role": "assistant", "content": "  rock wins  "}}]}
        )
        == "rock wins"
    )


def test_extract_content_parts():
    raw = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "Riley throws rock. "},
                        {"type": "text", "text": "Rival throws scissors."},
                    ]
                }
            }
        ]
    }
    assert extract_chat_message_text(raw) == "Riley throws rock. Rival throws scissors."


def test_extract_choice_text_fallback():
    assert extract_chat_message_text({"choices": [{"text": "paper covers rock"}]}) == "paper covers rock"
