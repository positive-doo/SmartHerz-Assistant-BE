-- Run as a database administrator on the SmartHerz database before release.
-- This procedure shares the chat schema owner and exposes only an app 66 aggregate.
CREATE OR ALTER PROCEDURE chat.SmartHerzRecommendationCount
    @start datetime2,
    @end datetime2
AS
BEGIN
    SET NOCOUNT ON;

    SELECT COUNT_BIG(*)
    FROM chat.Threads AS t
    INNER JOIN chat.Messages AS m ON m.thread_id = t.thread_id
    WHERE t.app_id = 66
      AND m.created_at >= @start AND m.created_at < @end
      AND m.role = N'tool'
      AND JSON_VALUE(
          CASE WHEN ISJSON(m.augmented_prompt) = 1 THEN m.augmented_prompt END,
          '$.tool_names'
      ) IN (N'search_agrotour', N'get_tourism_catalogue');
END;
GO

GRANT EXECUTE ON OBJECT::chat.SmartHerzRecommendationCount TO [svc_posai_chat_rw];
