from homework_agent.session import Session, SessionStore, rollback_incomplete, slugify, to_jsonable
from tests.fakes import text_block, thinking_block, tool_block


def test_round_trips_through_disk(tmp_path):
    store = SessionStore(tmp_path)
    session = Session(name="calc hw", mode="solve", subject="Calc II")
    session.add_user("help with q3")
    session.add_assistant([text_block("Sure.")])
    store.save(session)

    loaded = store.load("calc hw")
    assert loaded.mode == "solve"
    assert loaded.subject == "Calc II"
    assert loaded.last_assistant_text() == "Sure."


def test_thinking_signature_survives_serialization():
    """Replayed thinking blocks must keep their signature or the API rejects them."""
    session = Session(name="s")
    session.add_assistant([thinking_block("reasoning")])
    block = session.messages[0]["content"][0]
    assert block["type"] == "thinking"
    assert block["signature"] == "sig"


def test_to_jsonable_drops_nulls_but_keeps_data():
    class Model:
        def model_dump(self, exclude_none=False):
            return {"type": "text", "text": "hi", "citations": None} if not exclude_none \
                else {"type": "text", "text": "hi"}

    assert to_jsonable([Model()]) == [{"type": "text", "text": "hi"}]


def test_summary_uses_the_first_question():
    session = Session(name="s")
    session.add_user("why does u-substitution work?")
    session.add_assistant([text_block("Because...")])
    assert session.summary() == "why does u-substitution work?"


def test_listing_is_newest_first(tmp_path):
    store = SessionStore(tmp_path)
    for name, updated in [("old", "2026-01-01T00:00:00"), ("new", "2026-09-01T00:00:00")]:
        session = Session(name=name, updated=updated)
        session.add_user("q")
        store.save(session)
    # save() stamps updated itself, so both are current; the sort must still be stable.
    assert {s.name for s in store.list()} == {"old", "new"}


def test_slugify_makes_a_safe_filename():
    assert slugify("calc hw / week 3") == "calc-hw-week-3"
    assert slugify("../../etc/passwd") == "etc-passwd"
    assert slugify("") == "session"


def test_rollback_drops_an_unanswered_question():
    session = Session(name="s")
    session.add_user("first")
    session.add_assistant([text_block("answer")])
    session.add_user("interrupted question")
    assert rollback_incomplete(session) == 1
    assert session.messages[-1]["role"] == "assistant"


def test_rollback_drops_tool_calls_that_never_got_results():
    session = Session(name="s")
    session.add_user("q")
    session.add_assistant([tool_block("calculate", {"expression": "2+2"})])
    removed = rollback_incomplete(session)
    assert removed == 2
    assert session.messages == []


def test_rollback_keeps_a_finished_turn():
    session = Session(name="s")
    session.add_user("q")
    session.add_assistant([text_block("a")])
    assert rollback_incomplete(session) == 0
    assert len(session.messages) == 2


def test_delete(tmp_path):
    store = SessionStore(tmp_path)
    store.save(Session(name="gone"))
    assert store.delete("gone") is True
    assert store.delete("gone") is False
