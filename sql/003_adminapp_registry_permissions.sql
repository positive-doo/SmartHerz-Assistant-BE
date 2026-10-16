-- Run once as a database administrator before deploying AdminApp key integration.
-- The backend keeps its existing SQL identity; only registry reads are added.
GRANT SELECT ON OBJECT::admin.AdminApiKeys TO [svc_posai_chat_rw];
GRANT SELECT ON OBJECT::admin.AdminApiKeyScopes TO [svc_posai_chat_rw];
GRANT SELECT ON OBJECT::admin.AdminApiKeyScopeAssignments TO [svc_posai_chat_rw];
