Persistent MCP jobs
===================

Manage validated snapshot IDs, detached update workers, locks and persistent job status. Fetching creates local evidence; update_model and resume_update launch workers. These operations require POSIX locking at runtime.

.. automodule:: app.mcp_jobs
