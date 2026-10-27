"""Canonical version-1 DDL from the existing production tables."""
SCHEMA_SQL = """

            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                vendor TEXT NOT NULL,
                model TEXT NOT NULL,
                role TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                sender_id TEXT NOT NULL REFERENCES agents(agent_id),
                recipient_id TEXT NOT NULL REFERENCES agents(agent_id),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                read_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_messages_inbox
                ON messages(recipient_id, conversation_id, id);
            CREATE INDEX IF NOT EXISTS idx_messages_conversation
                ON messages(conversation_id, id);
            

                CREATE TABLE IF NOT EXISTS workbench_sessions (
                    conversation_id TEXT PRIMARY KEY,
                    codex_thread_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workbench_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    recipient_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'failed', 'cancelled'))
                );

                CREATE INDEX IF NOT EXISTS idx_workbench_messages_conversation
                    ON workbench_messages(conversation_id, id);

                CREATE TABLE IF NOT EXISTS workbench_agent_sessions (
                    conversation_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (conversation_id, agent_id)
                );

                CREATE TABLE IF NOT EXISTS workbench_conversation_settings (
                    conversation_id TEXT PRIMARY KEY,
                    working_directory TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                

                CREATE TABLE IF NOT EXISTS collab_jobs (
                    job_id TEXT PRIMARY KEY, request TEXT NOT NULL, plan TEXT NOT NULL,
                    stopped INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS collab_batches (
                    job_id TEXT NOT NULL, batch_id TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(job_id,batch_id));
                CREATE TABLE IF NOT EXISTS collab_tasks (
                    job_id TEXT NOT NULL, task_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                    instruction TEXT NOT NULL, deps TEXT NOT NULL, batch_id TEXT,
                    state TEXT NOT NULL DEFAULT 'pending', result TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(job_id,task_id));
            

                CREATE TABLE IF NOT EXISTS collab_lifecycle (
                    job_id TEXT PRIMARY KEY, phase TEXT NOT NULL DEFAULT 'working',
                    round_limit INTEGER NOT NULL DEFAULT 20, used INTEGER NOT NULL DEFAULT 0,
                    questions TEXT NOT NULL DEFAULT '[]', final_text TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS collab_calls (
                    job_id TEXT NOT NULL, call_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                    role TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'reserved',
                    text TEXT NOT NULL DEFAULT '', payload TEXT,
                    PRIMARY KEY(job_id,call_id));
                CREATE TABLE IF NOT EXISTS collab_authorizations(job_id TEXT PRIMARY KEY,targets TEXT NOT NULL);
            

                CREATE TABLE IF NOT EXISTS collab_bindings (
                    job_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS collab_operations (
                    job_id TEXT NOT NULL, operation_id TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(job_id,operation_id));
                CREATE TABLE IF NOT EXISTS collab_events (
                    job_id TEXT NOT NULL, operation_id TEXT NOT NULL, kind TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    PRIMARY KEY(job_id,operation_id));
                CREATE TABLE IF NOT EXISTS collab_user_updates (
                    job_id TEXT NOT NULL, operation_id TEXT NOT NULL, content TEXT NOT NULL,
                    PRIMARY KEY(job_id,operation_id));
            
CREATE TABLE IF NOT EXISTS collaboration_entry_results (
    message_id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL, payload_json TEXT NOT NULL);
"""

# Version 2 adds execution metadata; none of these tables dispatch work.
EXECUTION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS execution_runs (
    run_id TEXT PRIMARY KEY NOT NULL,
    agent_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
    source_kind TEXT NOT NULL, source_job_id TEXT, source_call_id TEXT, source_message_id INTEGER,
    instance_id TEXT NOT NULL, config_snapshot TEXT NOT NULL,
    workspace_id TEXT, workspace_version INTEGER,
    state TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
    exit_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(exit_confirmed IN (0,1))
);
CREATE TABLE IF NOT EXISTS execution_domains (
    agent_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
    owner_run_id TEXT REFERENCES execution_runs(run_id), instance_id TEXT,
    fence INTEGER NOT NULL DEFAULT 0 CHECK(fence >= 0),
    state TEXT NOT NULL DEFAULT 'idle' CHECK(state IN ('idle','reserved','running','quiescing')),
    PRIMARY KEY(agent_id,conversation_id),
    CHECK((state='idle' AND owner_run_id IS NULL AND instance_id IS NULL)
       OR (state!='idle' AND owner_run_id IS NOT NULL AND instance_id IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS execution_sessions (
    agent_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
    adapter_id TEXT, backend_scope TEXT, config_fingerprint TEXT,
    workspace_id TEXT, workspace_version INTEGER,
    session_id TEXT, generation INTEGER NOT NULL DEFAULT 0 CHECK(generation >= 0),
    owner_run_id TEXT REFERENCES execution_runs(run_id), instance_id TEXT,
    state TEXT NOT NULL DEFAULT 'needs_validation' CHECK(state IN ('needs_validation','ready')),
    legacy_candidates_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(agent_id,conversation_id), UNIQUE(backend_scope,session_id),
    CHECK(state!='ready' OR (adapter_id IS NOT NULL AND backend_scope IS NOT NULL
        AND config_fingerprint IS NOT NULL AND workspace_id IS NOT NULL
        AND workspace_version IS NOT NULL AND session_id IS NOT NULL AND generation >= 1))
);
"""
