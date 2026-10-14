-- Run once against the existing backend database. Safe to run again.
-- The devplay schema must already exist; application requests never run DDL.
IF OBJECT_ID(N'devplay.SmartHerzKeyLinks', N'U') IS NULL
BEGIN
    CREATE TABLE [devplay].[SmartHerzKeyLinks] (
        [token_digest] char(64) NOT NULL
            CONSTRAINT [PK_SmartHerzKeyLinks] PRIMARY KEY,
        [pin_digest] char(64) NOT NULL,
        [key_digest] char(64) NOT NULL,
        [expires_at] datetime2(0) NOT NULL,
        [attempts] tinyint NOT NULL
            CONSTRAINT [DF_SmartHerzKeyLinks_attempts] DEFAULT (0)
    );
END;
