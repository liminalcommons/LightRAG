# LightRAG API and MCP Server - Quick Commands

This guide provides the direct commands to start the LightRAG API server and the LightRAG MCP server.
Ensure your virtual environments are activated and all prerequisites (dependencies, `.env` files) are correctly configured beforehand.

**Key Configuration (ensure these are set in your `.env` files):**
*   **LightRAG API Server (`c:/Users/liminalcommon/Documents/GitHub/LightRAG/.env`):**
    *   `GEMINI_API_KEY="AIzaSyCbKdD1z7XTcBH6RaO2t05bovhtVkiOehs"`
    *   `LLM_MODEL="gemini-2.5-flash-preview-05-20"`
    *   `JINA_API_KEY="jina_...YourJinaKey..."` (replace with your actual Jina key)
*   **LightRAG MCP Server (`c:/Users/liminalcommon/Documents/GitHub/LightRAG/lightrag-mcp/.env`):**
    *   `LIGHTRAG_API_BASE_URL="http://localhost:9621"`
    *   `LIGHTRAG_API_KEY="AIzaSyCbKdD1z7XTcBH6RaO2t05bovhtVkiOehs"`

---

## 1. Start LightRAG API Server (Port 9621)

**Terminal 1 (PowerShell):**

1.  **Navigate to the LightRAG project directory:**
    ```powershell
    cd c:/Users/liminalcommon/Documents/GitHub/LightRAG
    ```

2.  **Activate Virtual Environment:**
    ```powershell
    .\.venv\Scripts\Activate.ps1
    ```

3.  **Run the LightRAG API Server:**
    (This command uses the `GEMINI_API_KEY` and `LLM_MODEL` from your `.env` file)
    ```powershell
    .\.venv\Scripts\python.exe -X utf8 -m lightrag.api.lightrag_server --use-custom-bindings --host localhost --port 9621 --working-dir "C:\Users\liminalcommon\Documents\GitHub\liminalnetworkstate\knowledge_graph" --input-dir ./input
    ```

---

## 2. Start LightRAG MCP Server (Port 9622)

**Terminal 2 (PowerShell - New, Separate Terminal):**

1.  **Navigate to the `lightrag-mcp` project directory:**
    ```powershell
    cd c:/Users/liminalcommon/Documents/GitHub/LightRAG/lightrag-mcp
    ```

2.  **Remove Existing `.venv` (if problematic):**
    If you encounter an error like "The directory .venv exists, but it's not a virtual environment", remove the existing `.venv` directory first:
    ```powershell
    Remove-Item -Recurse -Force .\.venv
    ```
    *(Ensure no other terminals have this `.venv` active and close any related Python processes. If permission issues persist, try running PowerShell as Administrator for this command.)*

3.  **Create Python 3.13 Virtual Environment (using `uv`):**
    ```powershell
    uv venv --python 3.13
    ```

4.  **Activate the MCP Server's Virtual Environment:**
    ```powershell
    .\.venv\Scripts\Activate.ps1
    ```

5.  **Install `lightrag-mcp` dependencies into the new venv:**
    (Run this command while the `lightrag-mcp/.venv` is active and you are in the `lightrag-mcp` directory)
    ```powershell
    uv pip install -e .
    ```

6.  **Run the LightRAG MCP Server:**
    ```powershellDocument Management
    Scan
    Pipeline Status
    Clear
    Upload
    Uploaded Documents
    All (1)
    Completed (1)
    Processing (0)
    Pending (0)
    Failed (0)
    File Name
    Show
    
    python src/lightrag_mcp/main.py --host localhost --port 9621 --api-key AIzaSyCbKdD1z7XTcBH6RaO2t05bovhtVkiOehs
    ```
    *(This command uses the new API key for clients connecting to the MCP server. The MCP server itself will use the `LIGHTRAG_API_KEY` from its `.env` file to connect to the API server).*

---