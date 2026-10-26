-- Register the weather tool only for SMART-HERZ (client 18, app 66).
-- Runtime executes the local weather.py adapter; no separate MCP is required.
SET XACT_ABORT ON;
BEGIN TRANSACTION;

IF NOT EXISTS (SELECT 1 FROM core.Applications WHERE app_id = 66 AND client_id = 18)
    THROW 50000, 'SMART-HERZ application identity does not match.', 1;

DECLARE @tool_id int;
SELECT @tool_id = tool_id FROM ai.Tools WITH (UPDLOCK, HOLDLOCK)
WHERE tool_name = N'get_weather';

IF @tool_id IS NULL
BEGIN
    INSERT INTO ai.Tools (tool_name, return_desc, comment, is_active)
    VALUES (N'get_weather',
        N'JSON weather forecast: local timestamps, temperature in Celsius, wind in km/h, precipitation in mm with its interval, and MET Norway attribution.',
        N'SMART-HERZ local backend tool; MET Norway Locationforecast 2.0. No API key or additional service.', 1);
    SET @tool_id = SCOPE_IDENTITY();
END;

IF NOT EXISTS (SELECT 1 FROM ai.Tools WHERE tool_id = @tool_id AND is_active = 1)
    THROW 50000, 'Existing weather tool is inactive; review before enabling.', 1;

IF NOT EXISTS (SELECT 1 FROM ai.AppTools WITH (UPDLOCK, HOLDLOCK) WHERE app_id = 66 AND tool_id = @tool_id)
BEGIN
    INSERT INTO ai.AppTools (app_id, tool_id, tool_name_sr, tool_description, tool_description_sr, input_params, is_enabled)
    VALUES (66, @tool_id, N'Vremenska prognoza',
        N'Get temperature, rain and wind forecasts for the requested Eastern Herzegovina municipality and dates. Today and the next six days; Europe/Sarajevo. Use returned facts and credit MET Norway. Ask for a municipality if it is unknown.',
        N'Prognoza temperature, padavina i vetra za osam opstina istocne Hercegovine. Danas i narednih sest dana. Izvor: MET Norway.',
        N'{"municipality":"trebinje|bileca|berkovici|kalinovik|nevesinje|gacko|ljubinje|istocni-mostar","date_from":"YYYY-MM-DD or null (today)","date_to":"YYYY-MM-DD or null (same day)"}',
        1);
END;

COMMIT TRANSACTION;
