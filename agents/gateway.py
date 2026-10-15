"""One public host: portal (Flask), citizen MCP, officer MCP, RFC 9728 metadata, /eval-dashboard."""
import contextlib
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from a2wsgi import WSGIMiddleware

from . import common as C
from .citizen_server import mcp as citizen
from .officer_server import mcp as officer
from .dashboard import dashboard_routes

C.ensure_schema()
citizen_app = citizen.streamable_http_app()
officer_app = officer.streamable_http_app()


@contextlib.asynccontextmanager
async def lifespan(app):
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(citizen.session_manager.run())
        await stack.enter_async_context(officer.session_manager.run())
        yield


def _prm(resource, scope):
    from starlette.responses import JSONResponse
    async def h(request):
        # exact issuer string (no trailing slash) so clients can match it to the AS metadata
        return JSONResponse({"resource": resource, "authorization_servers": [C.PUBLIC_BASE_URL],
                             "scopes_supported": [scope], "bearer_methods_supported": ["header"]},
                            headers={"Access-Control-Allow-Origin": "*"})
    return h


prm_routes = [Route("/.well-known/oauth-protected-resource/mcp/citizen", _prm(C.CITIZEN_URL, "citizen")),
              Route("/.well-known/oauth-protected-resource/mcp/officer", _prm(C.OFFICER_URL, "officer"))]
site = Starlette(routes=prm_routes + dashboard_routes() + [Mount("/", app=WSGIMiddleware(C.portal.app))])


async def app(scope, receive, send):
    """Each MCP server keeps its own auth middleware; everything else is the portal."""
    if scope["type"] == "lifespan":
        async with lifespan(None):
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
    path = scope.get("path", "")
    if path.startswith("/mcp/citizen"):
        return await citizen_app(scope, receive, send)
    if path.startswith("/mcp/officer"):
        return await officer_app(scope, receive, send)
    return await site(scope, receive, send)
