from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from race.auth import User, current_user
from race.models import (
    CloneRequest,
    CreateResource,
    Kind,
    QueryRequest,
    RegisterSource,
    ShareRequest,
    UpdateResource,
)

router = APIRouter(prefix="/api")
Identity = Annotated[User, Depends(current_user)]


def service(request: Request):
    return request.app.state.service


Service = Annotated[object, Depends(service)]


@router.get("/me")
def me(request: Request, user: Identity):
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "mode": request.app.state.settings.mode,
    }


@router.get("/tree")
def tree(user: Identity, svc: Service):
    return svc.tree(user)


@router.get("/sources")
def sources(user: Identity, svc: Service):
    return svc.sources(user)


@router.post("/sources", status_code=201)
def register_source(body: RegisterSource, user: Identity, svc: Service):
    return svc.register_source(body, user)


@router.post("/sheets/{rid}/query")
def query_sheet(rid: str, body: QueryRequest, user: Identity, svc: Service):
    return svc.query_sheet(rid, body, user)


@router.get("/charts/{rid}/data")
def query_chart(rid: str, user: Identity, svc: Service):
    return svc.query_chart(rid, user)


@router.post("/workbooks/{rid}/clone", status_code=201)
def clone(rid: str, body: CloneRequest, user: Identity, svc: Service):
    return svc.clone(rid, body, user)


@router.get("/resources/{kind}/{rid}/permissions")
def permissions(kind: Kind, rid: str, user: Identity, svc: Service):
    return svc.permissions(kind, rid, user)


@router.put("/resources/{kind}/{rid}/permissions")
def share(kind: Kind, rid: str, body: ShareRequest, user: Identity, svc: Service):
    return svc.share(kind, rid, user, body.principal_id, body.role, body.version)


@router.delete("/resources/{kind}/{rid}/permissions/{principal_id}")
def unshare(
    kind: Kind,
    rid: str,
    principal_id: str,
    user: Identity,
    svc: Service,
    version: Annotated[int, Query(ge=1)],
):
    return svc.share(kind, rid, user, principal_id, None, version)


@router.post("/resources/{kind}", status_code=201)
def create(kind: Kind, body: CreateResource, user: Identity, svc: Service):
    return svc.create(kind, body, user)


@router.get("/resources/{kind}/{rid}")
def get(kind: Kind, rid: str, user: Identity, svc: Service):
    return svc.get(kind, rid, user)


@router.patch("/resources/{kind}/{rid}")
def update(kind: Kind, rid: str, body: UpdateResource, user: Identity, svc: Service):
    return svc.update(kind, rid, body, user)


@router.delete("/resources/{kind}/{rid}", status_code=204)
def delete(kind: Kind, rid: str, user: Identity, svc: Service, version: Annotated[int, Query(ge=1)]):
    svc.delete(kind, rid, version, user)
    return Response(status_code=204)
