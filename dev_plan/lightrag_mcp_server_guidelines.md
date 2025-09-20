# LightRAG MCP Server - Operational Guidelines

This document contains best practices, command patterns, and troubleshooting steps for using the `lightrag-mcp server` tool. It is a living document and must be updated with all new learnings.

## Table of Contents
1.  [General Methodology](#general-methodology)
2.  [Troubleshooting Procedures](#troubleshooting-procedures)
3.  [Action-Specific Best Practices](#action-specific-best-practices)

---

## 1. General Methodology

All operations must follow the **Plan -> Execute -> Verify/Document** lifecycle.
- **Plan:** Clearly define the objective, identify the correct `lightrag-mcp` action, review these guidelines, and formulate the exact command(s).
- **Execute:** Run commands methodically and scrutinize the output for success or failure.
- **Verify & Document:** Confirm the outcome meets the objective. Document any new patterns, workarounds, or unexpected behaviors in this file immediately.

---

## 2. Troubleshooting Procedures

### T-401: Diagnosing 401 Unauthorized Errors

A `401 Unauthorized` error indicates an authentication failure. This can occur between the client and the MCP server, or between the MCP server and the LightRAG API, or between the LightRAG API and a downstream service (e.g., an LLM provider).

**Initial Diagnostic Steps:**

1.  **Check Core API Health:** The first step is always to verify the LightRAG API server is running and accessible. This isolates network or server-down issues from authentication problems.
    - **Command:** `use_mcp_tool` with `server_name: 'lightrag-mcp'` and `tool_name: 'check_lightrag_health'`.
    - **Expected Success:** A JSON response indicating the service is healthy, e.g., `{"status": "healthy", "services": {"database": "ok", "llm": "ok"}}`.
    - **Expected Failure:** A connection error or a non-200 status code. This points to a fundamental issue with the API server itself (not running, crashed, wrong port/host).

2.  **Verify End-to-End Authentication:** If the health check is successful, attempt a simple, low-impact operation that requires authentication.
    - **Command:** `use_mcp_tool` with `server_name: 'lightrag-mcp'`, `tool_name: 'query_document'`, and `arguments: {"query": "test"}`.
    - **If this fails with 401:** The issue is likely with the API key used for operations.
        - Check the `.env` file for the LightRAG API server (e.g., `GEMINI_API_KEY`).
        - Check the `.env` file for the MCP server (`LIGHTRAG_API_KEY`) to ensure it has the correct key to authenticate with the API server.
        - Ensure the server process was restarted after any `.env` changes to load the new configuration.

---

## 3. Action-Specific Best Practices

### `check_lightrag_health`
- **Purpose:** A quick, non-destructive check to see if the LightRAG API server is online and its core dependencies (like the database) are connected.
- **When to Use:** As the very first step when troubleshooting any connectivity or availability issue.

### `query_document`
- **Purpose:** To execute a search query. Can be used as a simple end-to-end test of the entire data pipeline, from query input to LLM response generation.
- **Note:** A 401 error on this action after a successful health check strongly implies an issue with the credentials required for data processing (e.g., the LLM API key).
