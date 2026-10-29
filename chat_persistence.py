"""Store completed anonymous chat turns in the existing SQL chat tables."""
import json
import logging
import uuid

from database import sql_connection

logger = logging.getLogger(__name__)


def record_chat_turn(
    *,
    thread_id: str,
    app_id: int,
    client_id: int,
    query: str,
    assistant_text: str,
    tool_runs: list[dict],
    filters: dict | None = None,
) -> bool:
    """Commit one whole turn, or roll it back without breaking the chat response."""
    try:
        metadata = {"turn_id": uuid.uuid4().hex, "client_id": client_id}
        user_metadata = {**metadata, "filters": filters} if filters else metadata
        messages = [("user", query, user_metadata)]
        for run in tool_runs:
            messages.append(
                (
                    "tool",
                    run["output"],
                    {
                        **metadata,
                        "tool_names": run["name"],
                        "call_id": run["call_id"],
                        "arguments": run["arguments"],
                    },
                )
            )
        messages.append(("assistant", assistant_text, metadata))

        with sql_connection() as connection:
            connection.timeout = 15
            try:
                cursor = connection.cursor()
                cursor.execute(
                    """SET NOCOUNT ON;
                       UPDATE chat.Threads WITH (UPDLOCK, HOLDLOCK)
                       SET last_message_at = SYSUTCDATETIME()
                       WHERE thread_id = ? AND app_id = ?;
                       IF @@ROWCOUNT = 0
                       BEGIN
                           IF EXISTS (SELECT 1 FROM chat.Threads WHERE thread_id = ?)
                               THROW 50000, 'Chat thread belongs to another application.', 1;
                           INSERT INTO chat.Threads
                               (thread_id, app_id, created_at, last_message_at)
                           VALUES (?, ?, SYSUTCDATETIME(), SYSUTCDATETIME());
                       END;""",
                    thread_id, app_id, thread_id, thread_id, app_id,
                )
                for role, text, details in messages:
                    cursor.execute(
                        """INSERT INTO chat.Messages
                           (thread_id, sender, role, message_text, augmented_prompt)
                           VALUES (?, ?, ?, ?, ?);""",
                        thread_id,
                        "Anon",
                        role,
                        text,
                        json.dumps(details, ensure_ascii=False),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return True
    except Exception as exc:
        # Do not log message text, tool payloads, connection strings or SQL errors.
        logger.warning("Chat turn could not be recorded: %s", type(exc).__name__)
        return False
