"""
LightRAG FastAPI Server
"""

from fastapi import FastAPI, Depends, HTTPException, status
import asyncio
import os
import logging
import logging.config
import uvicorn
import pipmaster as pm
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pathlib import Path
import configparser
from ascii_colors import ASCIIColors
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from lightrag.api.utils_api import (
    get_combined_auth_dependency,
    display_splash_screen,
    check_env_file,
)
from .config import (
    global_args,
    update_uvicorn_mode_config,
    get_default_host,
)
from lightrag.utils import get_env_value
import sys
from lightrag import LightRAG, __version__ as core_version
from lightrag.api import __api_version__
from lightrag.types import GPTKeywordExtractionFormat
from lightrag.utils import EmbeddingFunc

# --- Python Path Modification and Custom Function Imports START ---
import sys # sys was already imported, ensure it's fine
import os # os was already imported
# Assuming lightrag_server.py is in .../LightRAG/lightrag/api/
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from run_lightrag_gemini_jina import gemini_llm_complete_func, jina_embedding_func
    # Assuming jina_embedding_func from run_lightrag_gemini_jina.py is an EmbeddingFunc instance or compatible
    custom_functions_available = True
    print("INFO: Successfully imported custom functions from run_lightrag_gemini_jina.py.")
except ImportError as e:
    print(f"DEBUG: Could not import custom functions from run_lightrag_gemini_jina.py: {e}") # Optional debug
    gemini_llm_complete_func = None
    jina_embedding_func = None
    custom_functions_available = False
# --- Python Path Modification and Custom Function Imports END ---

from lightrag.constants import (
    DEFAULT_LOG_MAX_BYTES,
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_FILENAME,
)
from lightrag.api.routers.document_routes import (
    DocumentManager,
    create_document_routes,
    run_scanning_process,
)
from lightrag.api.routers.query_routes import create_query_routes
from lightrag.api.routers.graph_routes import create_graph_routes
from lightrag.api.routers.ollama_api import OllamaAPI

from lightrag.utils import logger, set_verbose_debug
from lightrag.kg.shared_storage import (
    get_namespace_data,
    get_pipeline_status_lock,
    initialize_pipeline_status,
)
from fastapi.security import OAuth2PasswordRequestForm
from lightrag.api.auth import auth_handler

# use the .env that is inside the current folder
# allows to use different .env file for each lightrag instance
# the OS environment variables take precedence over the .env file
load_dotenv(dotenv_path=".env", override=False)
# Removed existing custom function definitions as they are now imported.

webui_title = os.getenv("WEBUI_TITLE")
webui_description = os.getenv("WEBUI_DESCRIPTION")

# Initialize config parser
config = configparser.ConfigParser()
config.read("config.ini")

# Global authentication configuration
auth_configured = bool(auth_handler.accounts)


def create_app(args):
    # Setup logging
    logger.setLevel(args.log_level)
    set_verbose_debug(args.verbose)

    # Verify that bindings are correctly setup
    # Note: config.py already validates choices, but extra check here is fine
    supported_llm_bindings = ["lollms", "ollama", "openai", "openai-ollama", "azure_openai", "gemini"]
    supported_embedding_bindings = ["lollms", "ollama", "openai", "azure_openai", "gemini", "jina"]

    if args.llm_binding not in supported_llm_bindings:
        raise ValueError(f"Unsupported llm binding: {args.llm_binding}. Supported: {supported_llm_bindings}")

    if args.embedding_binding not in supported_embedding_bindings:
         raise ValueError(f"Unsupported embedding binding: {args.embedding_binding}. Supported: {supported_embedding_bindings}")

    # Set default hosts if not provided (config.py handles this now, but keep for clarity)
    if args.llm_binding_host is None:
        args.llm_binding_host = get_default_host(args.llm_binding)

    if args.embedding_binding_host is None:
        args.embedding_binding_host = get_default_host(args.embedding_binding)

    # Add SSL validation
    if args.ssl:
        if not args.ssl_certfile or not args.ssl_keyfile:
            raise Exception(
                "SSL certificate and key files must be provided when SSL is enabled"
            )
        if not os.path.exists(args.ssl_certfile):
            raise Exception(f"SSL certificate file not found: {args.ssl_certfile}")
        if not os.path.exists(args.ssl_keyfile):
            raise Exception(f"SSL key file not found: {args.ssl_keyfile}")

    # Check if API key is provided either through env var or args
    api_key = os.getenv("LIGHTRAG_API_KEY") or args.key

    # Initialize document manager
    doc_manager = DocumentManager(args.input_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Lifespan context manager for startup and shutdown events"""
        # Store background tasks
        app.state.background_tasks = set()

        try:
            # Initialize database connections
            await rag.initialize_storages()

            await initialize_pipeline_status()
            pipeline_status = await get_namespace_data("pipeline_status")

            should_start_autoscan = False
            async with get_pipeline_status_lock():
                # Auto scan documents if enabled
                if args.auto_scan_at_startup:
                    if not pipeline_status.get("autoscanned", False):
                        pipeline_status["autoscanned"] = True
                        should_start_autoscan = True

            # Only run auto scan when no other process started it first
            if should_start_autoscan:
                # Create background task
                task = asyncio.create_task(run_scanning_process(rag, doc_manager))
                app.state.background_tasks.add(task)
                task.add_done_callback(app.state.background_tasks.discard)
                logger.info(f"Process {os.getpid()} auto scan task started at startup.")

            ASCIIColors.green("\nServer is ready to accept connections! 🚀\n")

            yield

        finally:
            # Clean up database connections
            await rag.finalize_storages()

    # Initialize FastAPI
    app_kwargs = {
        "title": "LightRAG Server API",
        "description": "Providing API for LightRAG core, Web UI and Ollama Model Emulation"
        + "(With authentication)"
        if api_key
        else "",
        "version": __api_version__,
        "openapi_url": "/openapi.json",  # Explicitly set OpenAPI schema URL
        "docs_url": "/docs",  # Explicitly set docs URL
        "redoc_url": "/redoc",  # Explicitly set redoc URL
        "lifespan": lifespan,
    }

    # Configure Swagger UI parameters
    # Enable persistAuthorization and tryItOutEnabled for better user experience
    app_kwargs["swagger_ui_parameters"] = {
        "persistAuthorization": True,
        "tryItOutEnabled": True,
    }

    app = FastAPI(**app_kwargs)

    def get_cors_origins():
        """Get allowed origins from global_args
        Returns a list of allowed origins, defaults to ["*"] if not set
        """
        origins_str = global_args.cors_origins
        if origins_str == "*":
            return ["*"]
        return [origin.strip() for origin in origins_str.split(",")]

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Create combined auth dependency for all endpoints
    combined_auth = get_combined_auth_dependency(api_key)

    # Create working directory if it doesn't exist
    Path(args.working_dir).mkdir(parents=True, exist_ok=True)

    # --- Import necessary functions based on bindings ---
    if args.llm_binding == "lollms" or args.embedding_binding == "lollms":
        from lightrag.llm.lollms import lollms_model_complete, lollms_embed
    if args.llm_binding == "ollama" or args.embedding_binding == "ollama":
        from lightrag.llm.ollama import ollama_model_complete, ollama_embed
    if args.llm_binding == "openai" or args.embedding_binding == "openai":
        from lightrag.llm.openai import openai_complete_if_cache, openai_embed
    if args.llm_binding == "azure_openai" or args.embedding_binding == "azure_openai":
        from lightrag.llm.azure_openai import (
            azure_openai_complete_if_cache,
            azure_openai_embed,
        )
    # Handle openai-ollama combination explicitly if needed (config.py might already resolve this)
    if args.llm_binding == "openai" and args.embedding_binding == "ollama":
        # Imports likely already handled above, but good to be explicit if needed
        pass
    if args.llm_binding == "gemini" or args.embedding_binding == "gemini":
        from lightrag.llm.gemini import gemini_complete, gemini_embed

    # --- Define Helper Wrappers (indented correctly) ---
    async def openai_alike_model_complete(
        prompt,
        system_prompt=None,
        history_messages=None,
        keyword_extraction=False,
        **kwargs,
    ) -> str:
        keyword_extraction = kwargs.pop("keyword_extraction", None)
        if keyword_extraction:
            kwargs["response_format"] = GPTKeywordExtractionFormat
        if history_messages is None:
            history_messages = []
        # Temperature is now passed via llm_model_kwargs
        # kwargs["temperature"] = args.temperature
        return await openai_complete_if_cache(
            args.llm_model,
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages,
            base_url=args.llm_binding_host,
            api_key=args.llm_binding_api_key,
            **kwargs, # Pass remaining kwargs including temperature
        )

    async def azure_openai_model_complete(
        prompt,
        system_prompt=None,
        history_messages=None,
        keyword_extraction=False,
        **kwargs,
    ) -> str:
        keyword_extraction = kwargs.pop("keyword_extraction", None)
        if keyword_extraction:
            kwargs["response_format"] = GPTKeywordExtractionFormat
        if history_messages is None:
            history_messages = []
        # Temperature is now passed via llm_model_kwargs
        # kwargs["temperature"] = args.temperature
        return await azure_openai_complete_if_cache(
            args.llm_model,
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages,
            base_url=args.llm_binding_host,
            api_key=os.getenv("AZURE_OPENAI_API_KEY"), # Key still read from env here
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
            **kwargs, # Pass remaining kwargs including temperature
        )

    # --- Determine LLM and Embedding functions based on bindings or custom flag ---
    final_llm_func_to_use = None
    final_embedding_func_to_use = None # This will be an EmbeddingFunc instance

    # LLM Function Selection
    if args.use_custom_bindings and custom_functions_available and gemini_llm_complete_func:
        print("INFO: Using custom Gemini LLM function from run_lightrag_gemini_jina.py.")
        final_llm_func_to_use = gemini_llm_complete_func
    else:
        if args.use_custom_bindings and not (custom_functions_available and gemini_llm_complete_func):
            print("WARNING: --use-custom-bindings specified, but custom Gemini LLM function is not available. Falling back to CLI --llm-binding.")
        
        if args.llm_binding == "lollms":
            final_llm_func_to_use = lollms_model_complete
        elif args.llm_binding == "ollama":
            final_llm_func_to_use = ollama_model_complete
        elif args.llm_binding == "openai":
            final_llm_func_to_use = openai_alike_model_complete
        elif args.llm_binding == "azure_openai":
            final_llm_func_to_use = azure_openai_model_complete
        elif args.llm_binding == "gemini": # Standard gemini binding from lightrag.llm.gemini
            final_llm_func_to_use = gemini_complete
        else:
            # This case should ideally be caught by argparse choices, but as a safeguard:
            raise ValueError(f"Unsupported or misconfigured llm_binding: {args.llm_binding}")

    # Embedding Function Selection
    if args.use_custom_bindings and custom_functions_available and jina_embedding_func:
        print("INFO: Using custom Jina embedding function from run_lightrag_gemini_jina.py for embeddings.")
        if isinstance(jina_embedding_func, EmbeddingFunc):
            final_embedding_func_to_use = jina_embedding_func
        else: # Assuming it's a raw callable, wrap it in EmbeddingFunc
            # Attempt to get specific dim and max_tokens if defined in run_lightrag_gemini_jina.py
            # Fallback to command-line args if not found.
            # Example: JINA_EMBEDDING_DIM, JINA_MAX_TOKEN_SIZE could be constants in run_lightrag_gemini_jina.py
            try:
                from run_lightrag_gemini_jina import JINA_EMBEDDING_DIM, JINA_MAX_TOKEN_SIZE
                jina_dim = JINA_EMBEDDING_DIM
                jina_max_tokens = JINA_MAX_TOKEN_SIZE
                print(f"INFO: Using JINA_EMBEDDING_DIM={jina_dim}, JINA_MAX_TOKEN_SIZE={jina_max_tokens} from run_lightrag_gemini_jina.py")
            except ImportError:
                print(f"WARNING: JINA_EMBEDDING_DIM or JINA_MAX_TOKEN_SIZE not found in run_lightrag_gemini_jina.py. Using defaults from args: dim={args.embedding_dim}, max_tokens={args.max_embed_tokens}")
                jina_dim = args.embedding_dim
                jina_max_tokens = args.max_embed_tokens
            
            final_embedding_func_to_use = EmbeddingFunc(
                embedding_dim=jina_dim,
                max_token_size=jina_max_tokens,
                func=jina_embedding_func
            )
    else:
        if args.use_custom_bindings and not (custom_functions_available and jina_embedding_func):
            print("WARNING: --use-custom-bindings specified, but custom Jina embedding function is not available. Falling back to CLI --embedding-binding.")

        selected_embed_lambda = None
        if args.embedding_binding == "lollms":
            selected_embed_lambda = lambda texts: lollms_embed(
                texts,
                embed_model=args.embedding_model,
                host=args.embedding_binding_host,
                api_key=args.embedding_binding_api_key,
            )
        elif args.embedding_binding == "ollama":
            selected_embed_lambda = lambda texts: ollama_embed(
                texts,
                embed_model=args.embedding_model,
                host=args.embedding_binding_host,
                api_key=args.embedding_binding_api_key,
            )
        elif args.embedding_binding == "azure_openai":
            selected_embed_lambda = lambda texts: azure_openai_embed(
                texts,
                model=args.embedding_model,
                api_key=args.embedding_binding_api_key, # This should be AZURE_OPENAI_API_KEY from env
            )
        elif args.embedding_binding == "openai":
            selected_embed_lambda = lambda texts: openai_embed(
                texts,
                model=args.embedding_model,
                base_url=args.embedding_binding_host,
                api_key=args.embedding_binding_api_key,
            )
        elif args.embedding_binding == "gemini": # Standard gemini binding from lightrag.llm.gemini
             selected_embed_lambda = lambda texts: gemini_embed(
                texts,
                model_name=args.embedding_model, # e.g., "models/embedding-001"
                # api_key=os.getenv("GEMINI_API_KEY") # gemini_embed handles API key via genai.configure
            )
        else:
            # This case should ideally be caught by argparse choices, but as a safeguard:
            raise ValueError(f"Unsupported or misconfigured embedding_binding: {args.embedding_binding}")
        
        if selected_embed_lambda:
            final_embedding_func_to_use = EmbeddingFunc(
                embedding_dim=args.embedding_dim,
                max_token_size=args.max_embed_tokens,
                func=selected_embed_lambda
            )
        else: # Should not happen if choices are validated by argparse
            raise ValueError(f"Could not determine embedding function for binding: {args.embedding_binding}")


    # Common llm_model_kwargs, specific ones added based on binding if not custom
    llm_kwargs_for_rag = {}
    if not (args.use_custom_bindings and custom_functions_available and gemini_llm_complete_func):
        # Only populate these if not using custom LLM func, as custom func handles its own specifics
        llm_kwargs_for_rag = {
            "host": args.llm_binding_host, # For lollms, ollama, openai
            "timeout": args.timeout,
            "options": {"num_ctx": args.max_tokens}, # ollama/lollms specific
            "api_key": args.llm_binding_api_key, # for lollms, ollama, openai
        }
        if args.llm_binding == "azure_openai":
            llm_kwargs_for_rag["api_version"] = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
            # AZURE_OPENAI_API_KEY is read inside azure_openai_model_complete wrapper
            # Remove host/api_key if they are not used by azure_openai_model_complete directly
            llm_kwargs_for_rag.pop("host", None)
            llm_kwargs_for_rag.pop("api_key", None)
        elif args.llm_binding == "gemini": # Standard gemini
             # Gemini functions usually configured globally or handle API key internally
            llm_kwargs_for_rag.pop("host", None)
            llm_kwargs_for_rag.pop("api_key", None)
            llm_kwargs_for_rag.pop("options", None) # num_ctx not standard for Gemini
    else: # Using custom gemini_llm_complete_func
        llm_kwargs_for_rag = {
             "timeout": args.timeout, # General timeout can still be passed
             # Custom function handles its own model, host, api_key internally
        }


        logger.info(f"ROO_DEBUG: Final embedding function to use: {final_embedding_func_to_use}")
        if final_embedding_func_to_use:
            logger.info(f"ROO_DEBUG: Final embedding_func_to_use.embedding_dim: {final_embedding_func_to_use.embedding_dim}")
            logger.info(f"ROO_DEBUG: Final embedding_func_to_use.max_token_size: {final_embedding_func_to_use.max_token_size}")
            logger.info(f"ROO_DEBUG: Final embedding_func_to_use func: {final_embedding_func_to_use.func}")
        logger.info(f"ROO_DEBUG: args.embedding_dim: {args.embedding_dim}")
        logger.info(f"ROO_DEBUG: args.embedding_model: {args.embedding_model}")
        logger.info(f"ROO_DEBUG: args.embedding_binding: {args.embedding_binding}")
        logger.info(f"ROO_DEBUG: args.use_custom_bindings: {args.use_custom_bindings}")
        logger.info(f"ROO_DEBUG: custom_functions_available: {custom_functions_available}")
        if custom_functions_available and jina_embedding_func:
            try:
                from run_lightrag_gemini_jina import JINA_EMBEDDING_DIM
                logger.info(f"ROO_DEBUG: JINA_EMBEDDING_DIM from run_lightrag_gemini_jina: {JINA_EMBEDDING_DIM}")
            except ImportError:
                logger.info("ROO_DEBUG: JINA_EMBEDDING_DIM not found in run_lightrag_gemini_jina.py")

    rag = LightRAG(
        working_dir=args.working_dir,
        llm_model_func=final_llm_func_to_use,
        llm_model_name=args.llm_model, # Still useful for logging/reference even with custom func
        llm_model_max_async=args.max_async,
        llm_model_max_token_size=args.max_tokens,
        chunk_token_size=int(args.chunk_size),
        chunk_overlap_token_size=int(args.chunk_overlap_size),
        llm_model_kwargs=llm_kwargs_for_rag,
        embedding_func=final_embedding_func_to_use,
        kv_storage=args.kv_storage,
        graph_storage=args.graph_storage,
        vector_storage=args.vector_storage,
        doc_status_storage=args.doc_status_storage,
        vector_db_storage_cls_kwargs={
            "cosine_better_than_threshold": args.cosine_threshold
        },
        enable_llm_cache_for_entity_extract=args.enable_llm_cache_for_extract,
        enable_llm_cache=args.enable_llm_cache,
        auto_manage_storages_states=False, # Explicitly False as per original
        max_parallel_insert=args.max_parallel_insert,
        addon_params={"language": args.summary_language},
    )
    # Removed redundant LightRAG initialization for azure_openai as it's now covered by the general logic

    # --- Add routes (indented correctly) ---
    app.include_router(create_document_routes(rag, doc_manager, api_key))
    app.include_router(create_query_routes(rag, api_key, args.top_k))
    app.include_router(create_graph_routes(rag, api_key))

    # Add Ollama API routes
    ollama_api = OllamaAPI(rag, top_k=args.top_k, api_key=api_key)
    app.include_router(ollama_api.router, prefix="/api")

    @app.get("/")
    async def redirect_to_webui():
        """Redirect root path to /webui"""
        return RedirectResponse(url="/webui")

    @app.get("/auth-status")
    async def get_auth_status():
        """Get authentication status and guest token if auth is not configured"""

        if not auth_handler.accounts:
            # Authentication not configured, return guest token
            guest_token = auth_handler.create_token(
                username="guest", role="guest", metadata={"auth_mode": "disabled"}
            )
            return {
                "auth_configured": False,
                "access_token": guest_token,
                "token_type": "bearer",
                "auth_mode": "disabled",
                "message": "Authentication is disabled. Using guest access.",
                "core_version": core_version,
                "api_version": __api_version__,
                "webui_title": webui_title,
                "webui_description": webui_description,
            }

        return {
            "auth_configured": True,
            "auth_mode": "enabled",
            "core_version": core_version,
            "api_version": __api_version__,
            "webui_title": webui_title,
            "webui_description": webui_description,
        }

    @app.post("/login")
    async def login(form_data: OAuth2PasswordRequestForm = Depends()):
        if not auth_handler.accounts:
            # Authentication not configured, return guest token
            guest_token = auth_handler.create_token(
                username="guest", role="guest", metadata={"auth_mode": "disabled"}
            )
            return {
                "access_token": guest_token,
                "token_type": "bearer",
                "auth_mode": "disabled",
                "message": "Authentication is disabled. Using guest access.",
                "core_version": core_version,
                "api_version": __api_version__,
                "webui_title": webui_title,
                "webui_description": webui_description,
            }
        username = form_data.username
        if auth_handler.accounts.get(username) != form_data.password:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect credentials"
            )

        # Regular user login
        user_token = auth_handler.create_token(
            username=username, role="user", metadata={"auth_mode": "enabled"}
        )
        return {
            "access_token": user_token,
            "token_type": "bearer",
            "auth_mode": "enabled",
            "core_version": core_version,
            "api_version": __api_version__,
            "webui_title": webui_title,
            "webui_description": webui_description,
        }

    @app.get("/health", dependencies=[Depends(combined_auth)])
    async def get_status():
        """Get current system status"""
        try:
            pipeline_status = await get_namespace_data("pipeline_status")

            if not auth_configured:
                auth_mode = "disabled"
            else:
                auth_mode = "enabled"

            return {
                "status": "healthy",
                "working_directory": str(args.working_dir),
                "input_directory": str(args.input_dir),
                "configuration": {
                    # LLM configuration binding/host address (if applicable)/model (if applicable)
                    "llm_binding": args.llm_binding,
                    "llm_binding_host": args.llm_binding_host,
                    "llm_model": args.llm_model,
                    # embedding model configuration binding/host address (if applicable)/model (if applicable)
                    "embedding_binding": args.embedding_binding,
                    "embedding_binding_host": args.embedding_binding_host,
                    "embedding_model": args.embedding_model,
                    "max_tokens": args.max_tokens,
                    "kv_storage": args.kv_storage,
                    "doc_status_storage": args.doc_status_storage,
                    "graph_storage": args.graph_storage,
                    "vector_storage": args.vector_storage,
                    "enable_llm_cache_for_extract": args.enable_llm_cache_for_extract,
                    "enable_llm_cache": args.enable_llm_cache,
                },
                "auth_mode": auth_mode,
                "pipeline_busy": pipeline_status.get("busy", False),
                "core_version": core_version,
                "api_version": __api_version__,
                "webui_title": webui_title,
                "webui_description": webui_description,
            }
        except Exception as e:
            logger.error(f"Error getting health status: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    # Custom StaticFiles class to prevent caching of HTML files
    class NoCacheStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope):
            response = await super().get_response(path, scope)
            if path.endswith(".html"):
                response.headers["Cache-Control"] = (
                    "no-cache, no-store, must-revalidate"
                )
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
            return response

    # Webui mount webui/index.html
    static_dir = Path(__file__).parent / "webui"
    static_dir.mkdir(exist_ok=True)
    app.mount(
        "/webui",
        NoCacheStaticFiles(directory=static_dir, html=True, check_dir=True),
        name="webui",
    )

    return app # Ensure this is the last statement inside create_app


def get_application(args=None):
    """Factory function for creating the FastAPI application"""
    if args is None:
        args = global_args
    return create_app(args)


def configure_logging():
    """Configure logging for uvicorn startup"""

    # Reset any existing handlers to ensure clean configuration
    for logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error", "lightrag"]:
        logger = logging.getLogger(logger_name)
        logger.handlers = []
        logger.filters = []

    # Get log directory path from environment variable
    log_dir = os.getenv("LOG_DIR", os.getcwd())
    log_file_path = os.path.abspath(os.path.join(log_dir, DEFAULT_LOG_FILENAME))

    print(f"\nLightRAG log file: {log_file_path}\n")
    os.makedirs(os.path.dirname(log_dir), exist_ok=True)

    # Get log file max size and backup count from environment variables
    log_max_bytes = get_env_value("LOG_MAX_BYTES", DEFAULT_LOG_MAX_BYTES, int)
    log_backup_count = get_env_value("LOG_BACKUP_COUNT", DEFAULT_LOG_BACKUP_COUNT, int)

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(levelname)s: %(message)s",
                },
                "detailed": {
                    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                },
            },
            "handlers": {
                "console": {
                    "formatter": "default",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                },
                "file": {
                    "formatter": "detailed",
                    "class": "logging.handlers.RotatingFileHandler",
                    "filename": log_file_path,
                    "maxBytes": log_max_bytes,
                    "backupCount": log_backup_count,
                    "encoding": "utf-8",
                },
            },
            "loggers": {
                # Configure all uvicorn related loggers
                "uvicorn": {
                    "handlers": ["console", "file"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn.access": {
                    "handlers": ["console", "file"],
                    "level": "INFO",
                    "propagate": False,
                    "filters": ["path_filter"],
                },
                "uvicorn.error": {
                    "handlers": ["console", "file"],
                    "level": "INFO",
                    "propagate": False,
                },
                "lightrag": {
                    "handlers": ["console", "file"],
                    "level": "INFO",
                    "propagate": False,
                    "filters": ["path_filter"],
                },
            },
            "filters": {
                "path_filter": {
                    "()": "lightrag.utils.LightragPathFilter",
                },
            },
        }
    )


def check_and_install_dependencies():
    """Check and install required dependencies"""
    required_packages = [
        "uvicorn",
        "tiktoken",
        "fastapi",
        # Add other required packages here
    ]

    for package in required_packages:
        if not pm.is_installed(package):
            print(f"Installing {package}...")
            pm.install(package)
            print(f"{package} installed successfully")


def main():
    # Check if running under Gunicorn
    if "GUNICORN_CMD_ARGS" in os.environ:
        # If started with Gunicorn, return directly as Gunicorn will call get_application
        print("Running under Gunicorn - worker management handled by Gunicorn")
        return

    # Check .env file
    if not check_env_file():
        sys.exit(1)

    # Check and install dependencies
    check_and_install_dependencies()

    from multiprocessing import freeze_support

    freeze_support()

    # Configure logging before parsing args
    configure_logging()
    update_uvicorn_mode_config()
    display_splash_screen(global_args)

    # Create application instance directly instead of using factory function
    app = create_app(global_args)

    # Start Uvicorn in single process mode
    uvicorn_config = {
        "app": app,  # Pass application instance directly instead of string path
        "host": global_args.host,
        "port": global_args.port,
        "log_config": None,  # Disable default config
    }

    if global_args.ssl:
        uvicorn_config.update(
            {
                "ssl_certfile": global_args.ssl_certfile,
                "ssl_keyfile": global_args.ssl_keyfile,
            }
        )

    print(
        f"Starting Uvicorn server in single-process mode on {global_args.host}:{global_args.port}"
    )
    uvicorn.run(**uvicorn_config)


if __name__ == "__main__":
    main()
