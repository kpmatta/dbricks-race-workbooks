from dataclasses import dataclass, field

from fastapi import Request

from race.errors import AppError

@dataclass(frozen=True)
class User:
    id: str
    name: str
    email: str = ""
    token: str | None = field(default=None, repr=False)


def current_user(request: Request) -> User:
    settings = request.app.state.settings
    # Obtain the stable identity from a token verified by Databricks, never from a supplied user ID.
    from databricks.sdk import WorkspaceClient

    token = None
    if settings.mode == "databricks":
        token = request.headers.get("x-forwarded-access-token")
        if not token:
            raise AppError(401, "Databricks user authorization is required. Enable the sql scope.")
    try:
        if token:
            if not settings.workspace_host:
                raise ValueError("Missing Databricks workspace host")
            client = WorkspaceClient(host=settings.workspace_host, token=token, auth_type="pat")
        else:
            client_args = {}
            if settings.config_profile:
                client_args["profile"] = settings.config_profile
            if settings.workspace_host:
                client_args["host"] = settings.workspace_host
            client = WorkspaceClient(**client_args)
        me = client.current_user.me()
        if not me.id:
            raise ValueError("Missing identity")
        return User(str(me.id), me.display_name or me.user_name or str(me.id), me.user_name or "", token)
    except Exception:
        raise AppError(401, "Unable to verify your Databricks identity. Sign in again.") from None
