"""Story-independence and forbidden-encoding tests for P0-1 source documents."""

import pytest

from pacbench.source_documents import (
    SOURCE_DOCUMENT_MAPPING_VERSION,
    is_conversational,
    logical_document_key,
)


def _obs(source_id, channel, thread, sender="Someone", **extra):
    base = {
        "source_id": source_id,
        "channel": channel,
        "thread": thread,
        "sender": sender,
        "text": "hello",
        "written_at": "2026-01-05T09:00:00",
        "arrived_at": "2026-01-05T09:01:00",
        "collected_at": "2026-01-06T08:00:00",
        "from_owner": False,
        "structured": None,
    }
    base.update(extra)
    return base


def test_mapping_version_pinned():
    assert SOURCE_DOCUMENT_MAPPING_VERSION == "1.0"


def test_cross_life_same_conversation_one_key():
    """Same natural conversation in two lives/stories shares a document."""
    a = _obs("life-alpha/m1", "imessage", "chat-alex")
    b = _obs("life-beta/m7", "imessage", "chat-alex")
    assert logical_document_key(a) == logical_document_key(b)


def test_one_life_different_conversations_different_keys():
    a = _obs("life-alpha/m1", "imessage", "chat-alex")
    b = _obs("life-alpha/m2", "imessage", "chat-priya")
    assert logical_document_key(a) != logical_document_key(b)


def test_multi_sender_conversation_is_one_document():
    a = _obs("life-a/m1", "whatsapp", "wa-friends", sender="Nadia")
    b = _obs("life-a/m2", "whatsapp", "wa-friends", sender="Theo")
    assert logical_document_key(a) == logical_document_key(b)


def test_slack_thread_vs_channel_are_distinct():
    channel_msg = _obs("life-a/sl1", "slack", "C-general")
    reply = _obs("life-a/sl2", "slack", "C-general:thread-ts-123")
    assert logical_document_key(channel_msg) != logical_document_key(reply)


def test_slack_channel_messages_group_together():
    a = _obs("life-a/sl1", "slack", "C-general", sender="Devon")
    b = _obs("life-b/sl9", "slack", "C-general", sender="Sam")
    assert logical_document_key(a) == logical_document_key(b)


def test_slack_dm_is_conversational():
    a = _obs("life-a/d1", "slack", "DM-alex")
    b = _obs("life-a/d2", "slack", "DM-alex", sender="Alex")
    assert logical_document_key(a) == logical_document_key(b)


def test_email_same_sender_not_grouped():
    a = _obs("life-d-reunion/email-orig", "email", "email-nadia",
             sender="Nadia Reyes")
    b = _obs("life-d-reunion/email-confirm", "email", "email-nadia",
             sender="Nadia Reyes")
    assert logical_document_key(a) != logical_document_key(b)


def test_email_distinct_messages_distinct_keys():
    a = _obs("life-e/email-oldvisit", "email", "email-bistro")
    b = _obs("life-e/email-booked", "email", "email-bistro")
    assert logical_document_key(a) != logical_document_key(b)


def test_email_key_strips_life_prefix():
    key = logical_document_key(
        _obs("life-d-reunion/email-orig", "email", "email-nadia"))
    assert "life-d-reunion" not in key


def test_agent_alias_shares_hermes_document():
    a = _obs("life-a/h1", "hermes", "hermes", sender="me")
    b = _obs("life-a/h2", "agent", "hermes", sender="me")
    assert logical_document_key(a) == logical_document_key(b)


@pytest.mark.parametrize("channel", ["imessage", "text", "whatsapp",
                                     "groupme", "slack", "hermes", "agent"])
def test_is_conversational_channels(channel):
    assert is_conversational(channel) is True


def test_email_is_singleton():
    assert is_conversational("email") is False


def test_unknown_channel_fails_closed():
    with pytest.raises(ValueError):
        is_conversational("carrier-pigeon")


def test_no_forbidden_encodings_in_any_key():
    """No key may smuggle life, checkpoint, story, grading, or timing identity."""
    life_id = "life-d-reunion"
    obs_set = [
        _obs(f"{life_id}/wa-old-1", "whatsapp", "wa-crew",
             sender="Nadia Reyes"),
        _obs(f"{life_id}/email-orig", "email", "email-nadia",
             sender="Nadia Reyes"),
        _obs(f"{life_id}/slack-work", "slack", "slack-team",
             sender="Devon Park"),
        _obs(f"{life_id}/hermes-ack", "hermes", "hermes", sender="me"),
        _obs(f"{life_id}/sms-gate", "text", "sms-super", sender="Ray"),
        _obs(f"{life_id}/gm-1", "groupme", "poker-crew",
             sender="Sam Delgado"),
    ]
    # Benchmark-internal identity only: life/plan IDs, checkpoint IDs,
    # lineage/family strings, grading metadata, run timestamps, and the
    # plan.id/local separator. Source thread ids (e.g. "wa-crew") are
    # public provenance and legitimately appear in conversational keys.
    forbidden = [
        life_id,                       # life ID (+ plan.id/source_id prefix)
        "cp3", "checkpoint-3",         # checkpoint IDs
        "d-reunion", "reunion-family",  # story/family + lineage strings
        "grade", "rubric", "expected",  # grading metadata
        "2026-01-05", "2026-01-06",    # written/batching timestamps
        "09:00", "08:00",
        "/",                           # no residual plan.id/local split
    ]
    for obs in obs_set:
        key = logical_document_key(obs)
        for token in forbidden:
            assert token not in key, f"{token!r} leaked into key {key!r}"


def test_keys_use_only_public_fields():
    """Key function reads public Observation fields; authoring-side extras
    (participants, lineage, checkpoints) must not change the key."""
    plain = _obs("life-a/m1", "imessage", "chat-alex")
    enriched = _obs("life-a/m1", "imessage", "chat-alex",
                    participants=("Alex", "me"), lineage="reunion",
                    checkpoints=["cp3"])
    assert logical_document_key(plain) == logical_document_key(enriched)
