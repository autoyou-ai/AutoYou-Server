from pathlib import Path
from fastapi.responses import JSONResponse
from autoyou_agents.shared_tools.scheduler_mission_control import create_agent_website_app, _api_auth_error, _describe_auth_state
from shared.content_library_api import register_content_routes


def authorize(request):
    # A cross-agent catalog must never inherit an agent's optional public mode.
    auth = _describe_auth_state(request, "organizer_agent")
    if auth.get("via") == "open":
        return JSONResponse({"error": "The Organizer requires private, authenticated access. Choose gated access in agent settings."}, status_code=403)
    return _api_auth_error("organizer_agent", request)


def routes(app, agent_name):
    register_content_routes(app, authorize, prefix="/api/content")
    from shared.content_native import register_collection
    register_collection(app,authorize)


app = create_agent_website_app(agent_name="organizer_agent", title="Your content",
    description="A private home for your content across AutoYou agents.",
    index_path=Path(__file__).resolve().parents[1] / "frontend" / "index.html",
    extra_routes_fn=routes)


from shared.paired_content import install_collection_sync
install_collection_sync(app, "organizer_agent")
