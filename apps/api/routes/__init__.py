"""按业务边界拆分的 FastAPI 路由。"""

from .agent_profiles import create_agent_profile_router
from .chat import create_chat_router
from .health import create_health_router
from .providers import create_provider_router
from .reports import create_report_router
from .runs import create_run_router
from .session import create_session_router
from .threads import create_thread_router

__all__ = [
    "create_agent_profile_router",
    "create_chat_router",
    "create_health_router",
    "create_provider_router",
    "create_report_router",
    "create_run_router",
    "create_session_router",
    "create_thread_router",
]
