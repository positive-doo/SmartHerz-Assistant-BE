-- Run after 001_smartherz_key_links.sql in the existing backend database.
-- Additive and safe to run again; old link rows simply have no matching partner.
IF OBJECT_ID(N'devplay.SmartHerzPartners', N'U') IS NULL
BEGIN
    CREATE TABLE [devplay].[SmartHerzPartners] (
        [partner_id] nvarchar(100) NOT NULL
            CONSTRAINT [PK_SmartHerzPartners] PRIMARY KEY,
        [key_id] char(43) NOT NULL,
        [key_digest] char(64) NOT NULL
            CONSTRAINT [UQ_SmartHerzPartners_key_digest] UNIQUE,
        [created_at] datetime2(0) NOT NULL
            CONSTRAINT [DF_SmartHerzPartners_created_at] DEFAULT (SYSUTCDATETIME()),
        [revoked_at] datetime2(0) NULL
    );
END;
