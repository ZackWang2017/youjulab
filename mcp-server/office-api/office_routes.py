"""Browser HTTP adapter over the same functions registered as MCP tools."""
import os

from starlette.responses import JSONResponse

from office_agent import run_office_agent
from office_context import get_office_context
from office_patch import validate_office_patch


def register_routes(app):
    async def status(request):
        return JSONResponse({"ok": True, "model_configured": bool(
            os.environ.get("OFFICE_MODEL_BASE_URL") and os.environ.get("OFFICE_MODEL_NAME"))})

    async def dispatch(request):
        try:
            raw = await request.body()
            if len(raw) > 300000:
                return JSONResponse({"error": "Request too large"}, status_code=413)
            body = await request.json()
            action = request.path_params["action"]
            if action == "run":
                result = await run_office_agent(body["context"], body["intent"], body.get("mode", "demo"))
            elif action == "context":
                result = get_office_context(body["context"])
            elif action == "validate":
                result = validate_office_patch(body["patch"], body["context"])
            else:
                return JSONResponse({"error": "Not found"}, status_code=404)
            return JSONResponse(result)
        except (ValueError, KeyError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception:
            return JSONResponse({"error": "Agent request failed; check model endpoint and server logs"}, status_code=502)

    app.add_route("/api/office/status", status, methods=["GET"])
    app.add_route("/api/office/{action}", dispatch, methods=["POST"])
